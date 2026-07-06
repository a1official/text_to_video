$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$settingsPath = Join-Path $repoRoot ".env"

function Get-EnvValue([string]$name, [string]$default = "") {
    $value = [Environment]::GetEnvironmentVariable($name)
    if ([string]::IsNullOrWhiteSpace($value) -and (Test-Path $settingsPath)) {
        $match = Get-Content $settingsPath | Where-Object { $_ -match "^\s*$name=" } | Select-Object -First 1
        if ($match) {
            $value = ($match -split "=", 2)[1]
        }
    }
    if ([string]::IsNullOrWhiteSpace($value)) {
        return $default
    }
    return $value.Trim()
}

function Wait-CodeBuildBuild([string]$region, [string]$buildId) {
    while ($true) {
        $build = aws codebuild batch-get-builds --region $region --ids $buildId --query "builds[0]" --output json | ConvertFrom-Json
        $status = [string]$build.buildStatus
        Write-Host "CodeBuild status: $status"
        if ($status -in @("SUCCEEDED", "FAILED", "FAULT", "STOPPED", "TIMED_OUT")) {
            return $build
        }
        Start-Sleep -Seconds 20
    }
}

$region = Get-EnvValue "AWS_DEFAULT_REGION" "ap-south-1"
$accountId = (aws sts get-caller-identity --query Account --output text).Trim()
$roleArn = Get-EnvValue "SAGEMAKER_WAV2LIP_EXECUTION_ROLE_ARN" "arn:aws:iam::$accountId:role/SageMakerWav2LipExecutionRole"
$bucket = Get-EnvValue "SAGEMAKER_WAV2LIP_OUTPUT_BUCKET" (Get-EnvValue "S3_BUCKET")
$prefix = Get-EnvValue "SAGEMAKER_WAV2LIP_OUTPUT_PREFIX" "wav2lip-fast/outputs"
$endpointName = Get-EnvValue "SAGEMAKER_WAV2LIP_ENDPOINT_NAME" "wav2lip-fast-serverless"
$modelName = Get-EnvValue "SAGEMAKER_WAV2LIP_MODEL_NAME" "$endpointName-model"
$endpointConfigName = Get-EnvValue "SAGEMAKER_WAV2LIP_ENDPOINT_CONFIG_NAME" "$endpointName-config"
$repositoryName = Get-EnvValue "SAGEMAKER_WAV2LIP_ECR_REPOSITORY" "wav2lip-fast-serverless"
$imageTag = Get-EnvValue "SAGEMAKER_WAV2LIP_IMAGE_TAG" "latest"
$memoryMb = [int](Get-EnvValue "SAGEMAKER_WAV2LIP_MEMORY_MB" "6144")
$maxConcurrency = [int](Get-EnvValue "SAGEMAKER_WAV2LIP_MAX_CONCURRENCY" "1")
$repoUrl = Get-EnvValue "WAV2LIP_REPO_URL" "https://github.com/ohsugi/Wav2Lip-fast.git"
$repoRef = Get-EnvValue "WAV2LIP_REPO_REF" "master"
$checkpointS3Uri = Get-EnvValue "WAV2LIP_CHECKPOINT_S3_URI"
$codeBuildProjectName = Get-EnvValue "CODEBUILD_WAV2LIP_PROJECT_NAME" "wav2lip-fast-image-build"
$codeBuildRoleArn = Get-EnvValue "CODEBUILD_WAV2LIP_SERVICE_ROLE_ARN" "arn:aws:iam::${accountId}:role/CodeBuildWav2LipBuildRole"
$sourceKey = Get-EnvValue "CODEBUILD_WAV2LIP_SOURCE_KEY" "codebuild/wav2lip-fast/source.zip"

if ([string]::IsNullOrWhiteSpace($bucket)) {
    throw "Set SAGEMAKER_WAV2LIP_OUTPUT_BUCKET or S3_BUCKET in .env before deploying."
}

Write-Host "Using account: $accountId"
Write-Host "Using region: $region"
Write-Host "Using SageMaker role: $roleArn"
Write-Host "Using CodeBuild role: $codeBuildRoleArn"
Write-Host "Using output/source bucket: $bucket"

$repoUri = ""
try {
    $repoUri = (aws ecr describe-repositories --region $region --repository-names $repositoryName --query "repositories[0].repositoryUri" --output text 2>$null).Trim()
} catch {
    $repoUri = ""
}
if ([string]::IsNullOrWhiteSpace($repoUri) -or $repoUri -eq "None") {
    aws ecr create-repository --region $region --repository-name $repositoryName | Out-Null
    $repoUri = (aws ecr describe-repositories --region $region --repository-names $repositoryName --query "repositories[0].repositoryUri" --output text).Trim()
}

$imageUri = "$repoUri`:$imageTag"
$buildRoot = Join-Path $repoRoot "runtime\codebuild-wav2lip-fast"
$sourceDir = Join-Path $buildRoot "source"
$sourceZip = Join-Path $buildRoot "source.zip"
if (Test-Path $sourceDir) { Remove-Item -LiteralPath $sourceDir -Recurse -Force }
if (Test-Path $sourceZip) { Remove-Item -LiteralPath $sourceZip -Force }
New-Item -ItemType Directory -Path $sourceDir | Out-Null

Copy-Item -LiteralPath (Join-Path $repoRoot "infra\sagemaker-serverless\wav2lip-fast\Dockerfile") -Destination (Join-Path $sourceDir "Dockerfile")
Copy-Item -LiteralPath (Join-Path $repoRoot "infra\sagemaker-serverless\wav2lip-fast\serve.py") -Destination (Join-Path $sourceDir "serve.py")
Copy-Item -LiteralPath (Join-Path $repoRoot "infra\sagemaker-serverless\wav2lip-fast\serve") -Destination (Join-Path $sourceDir "serve")

$buildspec = @"
version: 0.2

phases:
  pre_build:
    commands:
      - aws ecr get-login-password --region `$AWS_DEFAULT_REGION | docker login --username AWS --password-stdin `$AWS_ACCOUNT_ID.dkr.ecr.`$AWS_DEFAULT_REGION.amazonaws.com
  build:
    commands:
      - docker build --build-arg WAV2LIP_REPO_URL="`$WAV2LIP_REPO_URL" --build-arg WAV2LIP_REPO_REF="`$WAV2LIP_REPO_REF" -t "`$IMAGE_URI" .
  post_build:
    commands:
      - docker push "`$IMAGE_URI"
"@
$buildspec | Set-Content -Path (Join-Path $sourceDir "buildspec.yml") -Encoding ascii
Compress-Archive -Path (Join-Path $sourceDir "*") -DestinationPath $sourceZip -Force

aws s3 cp $sourceZip "s3://$bucket/$sourceKey" --region $region | Out-Null

$projectExists = $false
try {
    $existingProjectName = (aws codebuild batch-get-projects --region $region --names $codeBuildProjectName --query "projects[0].name" --output text 2>$null).Trim()
    $projectExists = $existingProjectName -eq $codeBuildProjectName
} catch {
    $projectExists = $false
}

$environmentVariables = @(
    @{ name = "AWS_DEFAULT_REGION"; value = $region; type = "PLAINTEXT" },
    @{ name = "AWS_ACCOUNT_ID"; value = $accountId; type = "PLAINTEXT" },
    @{ name = "IMAGE_URI"; value = $imageUri; type = "PLAINTEXT" },
    @{ name = "WAV2LIP_REPO_URL"; value = $repoUrl; type = "PLAINTEXT" },
    @{ name = "WAV2LIP_REPO_REF"; value = $repoRef; type = "PLAINTEXT" }
)

$projectPayload = @{
    name = $codeBuildProjectName
    serviceRole = $codeBuildRoleArn
    artifacts = @{ type = "NO_ARTIFACTS" }
    source = @{
        type = "NO_SOURCE"
        buildspec = @"
version: 0.2

phases:
  install:
    commands:
      - apt-get update && apt-get install -y unzip
  pre_build:
    commands:
      - aws ecr get-login-password --region `$AWS_DEFAULT_REGION | docker login --username AWS --password-stdin `$AWS_ACCOUNT_ID.dkr.ecr.`$AWS_DEFAULT_REGION.amazonaws.com
      - aws s3 cp "s3://$bucket/$sourceKey" source.zip
      - unzip -q source.zip -d source
  build:
    commands:
      - cd source
      - docker build --build-arg WAV2LIP_REPO_URL="`$WAV2LIP_REPO_URL" --build-arg WAV2LIP_REPO_REF="`$WAV2LIP_REPO_REF" -t "`$IMAGE_URI" .
  post_build:
    commands:
      - docker push "`$IMAGE_URI"
"@
    }
    environment = @{
        type = "LINUX_CONTAINER"
        image = "aws/codebuild/standard:7.0"
        computeType = "BUILD_GENERAL1_MEDIUM"
        privilegedMode = $true
        environmentVariables = $environmentVariables
    }
    timeoutInMinutes = 60
}

$projectJson = Join-Path $buildRoot "codebuild-project.json"
$projectPayload | ConvertTo-Json -Depth 12 | Set-Content -Path $projectJson -Encoding ascii

if ($projectExists) {
    Write-Host "Updating CodeBuild project $codeBuildProjectName..."
    aws codebuild update-project --region $region --cli-input-json "file://$projectJson" | Out-Null
} else {
    Write-Host "Creating CodeBuild project $codeBuildProjectName..."
    aws codebuild create-project --region $region --cli-input-json "file://$projectJson" | Out-Null
}

$buildId = (aws codebuild start-build --region $region --project-name $codeBuildProjectName --query "build.id" --output text).Trim()
Write-Host "Started CodeBuild build: $buildId"
$build = Wait-CodeBuildBuild $region $buildId
if ([string]$build.buildStatus -ne "SUCCEEDED") {
    $logs = $build.logs
    if ($null -ne $logs) {
        if ($logs.PSObject.Properties.Name -contains "groupName") {
            Write-Host "CodeBuild log group: $($logs.groupName)"
        }
        if ($logs.PSObject.Properties.Name -contains "streamName") {
            Write-Host "CodeBuild log stream: $($logs.streamName)"
        }
    }
    throw "CodeBuild image build failed with status $($build.buildStatus)."
}

$modelExists = $false
try {
    aws sagemaker describe-model --region $region --model-name $modelName | Out-Null
    $modelExists = $true
} catch {
    $modelExists = $false
}

$modelPayload = @{
    ModelName = $modelName
    ExecutionRoleArn = $roleArn
    PrimaryContainer = @{
        Image = $imageUri
        Environment = @{
            SAGEMAKER_WAV2LIP_OUTPUT_BUCKET = $bucket
            SAGEMAKER_WAV2LIP_OUTPUT_PREFIX = $prefix
            WAV2LIP_CHECKPOINT_S3_URI = $checkpointS3Uri
            WAV2LIP_REPO_ROOT = "/opt/program/Wav2Lip-fast"
            MODEL_DIR = "/opt/ml/model"
            TEMP_DIR = "/tmp/wav2lip-fast"
        }
    }
}

$modelJson = Join-Path $repoRoot "runtime\sagemaker-wav2lip-fast-model.json"
$modelPayload | ConvertTo-Json -Depth 8 | Set-Content -Path $modelJson -Encoding ascii
if ($modelExists) {
    Write-Host "Model $modelName already exists; reusing it."
} else {
    aws sagemaker create-model --region $region --cli-input-json "file://$modelJson" | Out-Null
}

$endpointConfigExists = $false
try {
    aws sagemaker describe-endpoint-config --region $region --endpoint-config-name $endpointConfigName | Out-Null
    $endpointConfigExists = $true
} catch {
    $endpointConfigExists = $false
}

$endpointConfigPayload = @{
    EndpointConfigName = $endpointConfigName
    ProductionVariants = @(
        @{
            VariantName = "AllTraffic"
            ModelName = $modelName
            ServerlessConfig = @{
                MemorySizeInMB = $memoryMb
                MaxConcurrency = $maxConcurrency
            }
        }
    )
}

$endpointConfigJson = Join-Path $repoRoot "runtime\sagemaker-wav2lip-fast-endpoint-config.json"
$endpointConfigPayload | ConvertTo-Json -Depth 8 | Set-Content -Path $endpointConfigJson -Encoding ascii
if ($endpointConfigExists) {
    Write-Host "Endpoint config $endpointConfigName already exists; reusing it."
} else {
    aws sagemaker create-endpoint-config --region $region --cli-input-json "file://$endpointConfigJson" | Out-Null
}

$endpointExists = $false
try {
    aws sagemaker describe-endpoint --region $region --endpoint-name $endpointName | Out-Null
    $endpointExists = $true
} catch {
    $endpointExists = $false
}

if ($endpointExists) {
    aws sagemaker update-endpoint --region $region --endpoint-name $endpointName --endpoint-config-name $endpointConfigName | Out-Null
    Write-Host "Updating endpoint $endpointName..."
} else {
    aws sagemaker create-endpoint --region $region --endpoint-name $endpointName --endpoint-config-name $endpointConfigName | Out-Null
    Write-Host "Creating endpoint $endpointName..."
}

Write-Host "Waiting for endpoint to become InService..."
aws sagemaker wait endpoint-in-service --region $region --endpoint-name $endpointName

Write-Host "Done."
Write-Host "Endpoint name: $endpointName"
Write-Host "Model name: $modelName"
Write-Host "Endpoint config: $endpointConfigName"
Write-Host "Image: $imageUri"
