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

if ([string]::IsNullOrWhiteSpace($bucket)) {
    throw "Set SAGEMAKER_WAV2LIP_OUTPUT_BUCKET or S3_BUCKET in .env before deploying."
}

Write-Host "Using account: $accountId"
Write-Host "Using role: $roleArn"
Write-Host "Using output bucket: $bucket"

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
$loginPassword = aws ecr get-login-password --region $region
$loginPassword | docker login --username AWS --password-stdin "$accountId.dkr.ecr.$region.amazonaws.com" | Out-Null

$dockerfile = Join-Path $repoRoot "infra\sagemaker-serverless\wav2lip-fast\Dockerfile"
docker build `
    --build-arg WAV2LIP_REPO_URL="$repoUrl" `
    --build-arg WAV2LIP_REPO_REF="$repoRef" `
    -f $dockerfile `
    -t $imageUri `
    $repoRoot
if ($LASTEXITCODE -ne 0) { throw "Docker build failed." }

docker push $imageUri
if ($LASTEXITCODE -ne 0) { throw "Docker push failed." }

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
