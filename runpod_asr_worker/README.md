# Dayax RunPod Serverless worker

This worker loads `IbrahimDayax/xls-r-1b-somali-stt-asr` once when a GPU worker
starts. It accepts either normalized 16 kHz mono PCM WAV audio as base64 or one
public YouTube video URL. URL mode retrieves only the audio track inside the
RunPod worker, then converts and transcribes it without routing the download
through the Google Cloud application server.

Accepted inputs:

```json
{"input":{"audio_base64":"<base64 wav>"}}
```

or:

```json
{"input":{"media_url":"https://www.youtube.com/watch?v=VIDEO_ID"}}
```

Configure these RunPod endpoint environment variables:

- `HF_TOKEN` (required if the model repository requires authentication)
- `ASR_MODEL_ID=IbrahimDayax/xls-r-1b-somali-stt-asr`
- `ASR_CHUNK_SECONDS=28`
- `ASR_OVERLAP_SECONDS=1`
- `ASR_GPU_BATCH_SIZE=2`
- `MAX_MEDIA_DURATION_SECONDS=1800`
- `MAX_REMOTE_MEDIA_BYTES=104857600`
- `YOUTUBE_COOKIES_B64=<base64 Netscape cookie file>` (recommended when
  YouTube challenges the RunPod data-center IP)
- `YOUTUBE_PROXY_URL=<authenticated proxy URL>` (optional alternative)

In the endpoint's **Model** field, add:

```text
IbrahimDayax/xls-r-1b-somali-stt-asr
```

The custom worker resolves this model from RunPod's mounted Hugging Face cache
at `/runpod-volume/huggingface-cache/hub/`. This prevents every cold worker
from downloading the 1B model during the user's request. If the endpoint model
cache is not configured, the worker keeps the existing Hugging Face download
fallback and prints a clear startup message.

`YOUTUBE_COOKIES_B64` and `YOUTUBE_PROXY_URL` must be RunPod secrets, not
committed environment values. The worker materializes the cookie file only in
the request's temporary directory and never logs or returns its contents. The
cookie-assisted path uses the mweb player with a local PO-token provider.

After creating the endpoint, configure the Flask application's `.env`:

```text
ASR_BACKEND=remote
RUNPOD_API_KEY=<secret API key>
RUNPOD_ASR_ENDPOINT_ID=<endpoint id>
```

Keep the API key only in server-side environment variables. The browser never
calls RunPod directly.
