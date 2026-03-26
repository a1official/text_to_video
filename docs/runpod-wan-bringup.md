# Runpod Wan Bring-Up

This is the first compute bring-up path for the current project.

## Current Pod

- Pod name: `text2video-wan-worker`
- Pod id: `kr79sdozpzysfb`
- GPU: `RTX 6000 Ada`
- Cost: `$0.77/hr`

## What Happens Next

1. add an SSH public key to Runpod account settings
2. verify the pod has SSH info
3. copy this repo onto the pod
4. run the TI2V bootstrap script
5. render a worker `.env` file and upload it
6. start the remote inference service on the pod
7. point the local control plane at that service
8. run the local `wan` worker to dispatch jobs remotely

## Files Added For This

- `scripts/runpod/bootstrap-ti2v-services.sh`
- `scripts/runpod/start-inference-service.sh`
- `scripts/runpod/render-runpod-env.ps1`

## Local Commands

Render the worker env file:

```powershell
.\scripts\runpod\render-runpod-env.ps1
```

Package the repo for upload to the pod:

```powershell
.\scripts\runpod\package-repo.ps1
```

This writes:

```text
runtime/runpod/.env.runpod
```

The file contains only the values the remote worker needs.

The packaging step writes:

```text
runtime/runpod/text2video-runpod.zip
```

Upload that zip through JupyterLab or any browser file upload path available on the pod, then unpack it into `/workspace/text2video/app`.

## Pod Commands

Once the repo bundle is unpacked into `/workspace/text2video/app`:

```bash
cd /workspace/text2video
unzip -o text2video-runpod.zip -d app
chmod +x /workspace/text2video/app/scripts/runpod/bootstrap-ti2v-services.sh
chmod +x /workspace/text2video/app/scripts/runpod/start-inference-service.sh
/workspace/text2video/app/scripts/runpod/bootstrap-ti2v-services.sh
cp /workspace/text2video/app/runtime/runpod/.env.runpod /workspace/text2video/app/.env
/workspace/text2video/app/scripts/runpod/start-inference-service.sh
```

## Remaining Technical Gap

The remaining local step is setting `RUNPOD_INFERENCE_BASE_URL` in `.env` to the pod's HTTP URL and then running a local worker with `WORKER_TYPE=wan`.
