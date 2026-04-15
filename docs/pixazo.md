# Pixazo API — Complete Model Reference

> Auto-generated from [pixazo.ai/models](https://www.pixazo.ai/models) on 2026-04-15.
> 22 models, ~138 API endpoints.

## Common API Patterns

### Authentication
All requests require: `Ocp-Apim-Subscription-Key: YOUR_API_KEY`

### Async Workflow
1. `POST` generate endpoint → `{request_id, status: "QUEUED", polling_url}`
2. Poll `GET /v2/requests/status/{request_id}` every 5-10s
3. Status: `QUEUED → PROCESSING → COMPLETED` (or `FAILED`/`ERROR`)
4. Download from `output.media_url`

**Webhook alternative:** `X-Webhook-URL: https://your-server.com/callback`

### Status Endpoint
```
GET https://gateway.pixazo.ai/v2/requests/status/{request_id}
```

---

## Pricing Summary

> Prices in USD. $0 = free tier. Prices may vary by resolution/duration.

| Model | Type | Spec | Price (USD) |
|-------|------|------|-------------|
| ElevenLabs | TTS | per 1000 chars | $0.10 |
| Fashn | Virtual Try-On | per image | $0.075 |
| FireRed | Image Edit | per image | $0.016 |
| Flux 1 Schnell | Text to Image | all res | Free |
| Flux Dev | Text/Image to Image | all res | $0.025 |
| Flux Pro | Text to Image | all res | $0.03 |
| Flux Pro 1.1 | Text to Image | all res | $0.08 |
| Flux 2 Pro | Text to Image | all res | $0.06 |
| Flux 2 Klein | Text to Image | 512px | $0.0003 |
| Flux 2 Klein | Text to Image | 2048px | $0.0028 |
| Flux Fill Dev | Inpainting | all res | $0.04 |
| GPT Image 1.5 | Text to Image | - | not listed |
| Hunyuan 3.0 | Text to Image | varies | $0.11-0.40 |
| Hunyuan 3D | 3D Generation | - | $0.20 |
| Ideogram v2 | Text to Image | all res | $0.20 |
| Ideogram v2 | Describe Image | all res | $0.20 |
| Ideogram v2 | Edit Image | all res | $0.20 |
| Ideogram v2 | Remix Image | all res | $0.20 |
| Ideogram Turbo | Text to Image | all res | $0.20 |
| Ideogram Turbo | Describe Image | all res | $0.20 |
| Ideogram Turbo | Edit Image | all res | $0.20 |
| Ideogram Turbo | Remix Image | all res | $0.20 |
| Kling O3 | Text/Image to Image | 1-2K | $0.028 |
| Kling O3 | Text/Image to Image | 4K | $0.056 |
| Kling 3.0 | Text/Image to Video | 1s (no audio) | $0.168 |
| Kling 3.0 | Text/Image to Video | 1s (audio) | $0.252 |
| Kling v1.6 | Text/Image to Video | 1s | $0.07 |
| Kling Avatar v2 | AI Avatar | 1s | $0.115 |
| Kling O1 | Ref Image to Video | per gen | $0.90 |
| Lyria 3 Pro | Music Generation | per gen | $0.08 |
| Lyria 2 | Music Generation | per gen | $0.06 |
| Nano Banana Pro | Generate Image | 1-2K | $0.15 |
| Nano Banana Pro | Generate Image | 4K | $0.30 |
| Nano Banana 2 | Edit Image | 1K | $0.067 |
| Nano Banana Std | Text to Image | per gen | $0.06-0.16 |
| P Image | Upscale/Edit | 1-4MP | $0.005 |
| P Image | Upscale/Edit | 4-8MP | $0.01 |
| P Video | Video Gen | 720p/1s | $0.02 |
| P Video | Video Gen | 1080p/1s | $0.04 |
| Pika | Video Gen | - | coming soon |
| Recraft V4 Pro | Text to Image | per gen | $0.04-0.25 |
| Recraft V3 | Image to Image | per gen | $0.04-0.25 |
| Reve Image | Image Edit | per gen | $0.06 |
| Runway Gen-4.5 | Video Gen | 1s | $0.12 |
| Seedance 2.0 Fast | Text/Ref/Edit Video | 1s | $0.24 |
| Seedance 2.0 | Text/Ref/Edit Video | 1s | $0.29 |
| Seedance 1.0 Pro | Image/Text to Video | per gen | $0.20 |
| Seedream 5 | Text/Image to Image | per gen | $0.035 |
| Seedream 4.5 | Text/Image to Image | per gen | $0.20 |
| Sora 2 Pro | Image to Video | 720p/4s | $1.50 |
| Sora 2 Pro | Image to Video | 720p/8s | $3.00 |
| Sora 2 Pro | Image to Video | 1080p/4s | $2.50 |
| Sora 2 Pro | Image to Video | 1080p/8s | $5.00 |
| Studio Ghibli | Image Gen | varies | $0.01-0.04 |
| Veo 3.1 Fast | Video Gen | 1s (audio) | $0.15 |
| Veo 3.1 Fast | Video Gen | 1s (no audio) | $0.10 |
| Veo 3.1 | Video Gen | 1s | $1.80-3.60 |
| Wan 2.7 | Text/Edit Image | per gen | $0.03 |
| Wan 2.7 Pro | Text/Edit Image | per gen | $0.075 |
| Wan 2.6 | Image/Text to Video | 5s | $0.75 |
| Wan 2.6 | Image/Text to Video | 10s | $1.50 |
| Wan 2.5 | All operations | per gen | $0.05 |

---


## Quick Endpoint Reference

| Model | API ID | Operation | Method |
|-------|--------|-----------|--------|
| p-video | `p-video` | `p-video/generate` | POST |
| p-video | `p-video` | `p-video/prediction` | POST |
| seedance | `byteplus` | `generateFrame2VideoTask` | POST |
| seedance | `byteplus` | `generateImage2VideoTask` | POST |
| seedance | `byteplus` | `generateVideoTask` | POST |
| seedance | `seedance-2-0-fast` | `edit-video-fast` | POST |
| seedance | `seedance-2-0-fast` | `reference-to-video-fast` | POST |
| seedance | `seedance-2-0-fast` | `text-to-video-fast` | POST |
| seedance | `seedance-2-0` | `edit-video` | POST |
| seedance | `seedance-2-0` | `reference-to-video` | POST |
| seedance | `seedance-2-0` | `text-to-video` | POST |
| sora | `sora-video` | `video/generate` | POST |
| sora | `sora-video` | `video/i2v/generate` | POST |
| sora | `sora-video` | `video/result` | POST |
| veo | `veo` | `veo-3.1/generate` | POST |
| veo | `veo31f` | `veo-3.1-fast/generate` | POST |
| runway | `runway-gen-4-5` | `gen-4.5/generate` | POST |
| runway | `runway-gen-4-5` | `gen-4.5/prediction` | POST |
| kling | `kling-3-0-image-to-video-standard` | `kling-3-0-image-to-video-standard-request` | POST |
| kling | `kling-3-0-image-to-video-standard` | `kling-3-0-image-to-video-standard-request-result` | POST |
| kling | `kling-3-0-text-to-video-standard` | `kling-3-0-text-to-video-standard-request` | POST |
| kling | `kling-3-0-text-to-video-standard` | `kling-3-0-text-to-video-standard-request-result` | POST |
| kling | `kling-ai-avatar-v2-pro-789` | `kling-ai-avatar-v2-pro-request` | POST |
| kling | `kling-ai-video` | `generateVideoTask` | POST |
| kling | `kling-ai-video` | `getImageToVideoTask` | POST |
| kling | `kling-ai-vton` | `getVirtualTryOnTask` | POST |
| kling | `kling-image-o3-i2i` | `kling-image-o3-i2i-request` | POST |
| kling | `kling-image-t2i` | `kling-image-t2i-request` | POST |
| kling | `kling-image` | `kling-image-request` | POST |
| kling | `kling-o1-edit-video-video-to-video-634` | `kling-o1-edit-video-video-to-video-request` | POST |
| kling | `kling-o1-first-frame-last-frame-to-video-857` | `kling-o1-first-frame-last-frame-to-video-request` | POST |
| kling | `kling-o1-image-208` | `kling-o1-image-request` | POST |
| kling | `kling-o1-reference-image-to-video-382` | `kling-o1-reference-image-to-video-request` | POST |
| kling | `kling-o1-reference-video-to-video-315` | `kling-o1-reference-video-to-video-request` | POST |
| kling | `kling-video-v2-6-standard-motion-control` | `kling-video-v2-6-standard-motion-control-request` | POST |
| flux | `flux-1-schnell` | `checkStatus` | POST |
| flux | `flux-1-schnell` | `getData` | POST |
| flux | `flux-1-schnell` | `getDataBatch` | POST |
| flux | `flux-2-klein-4b` | `generateImage` | POST |
| flux | `flux-2-pro-image-to-image-866` | `flux-2-pro-image-to-image-request` | POST |
| flux | `flux-2-pro-image-to-image-trainer-831` | `flux-2-pro-image-to-image-trainer-request` | POST |
| flux | `flux-2-pro-text-to-image-799` | `flux-2-pro-text-to-image-request` | POST |
| flux | `flux-2-pro-text-to-image-trainer-712` | `flux-2-pro-text-to-image-trainer-request` | POST |
| flux | `flux-dev` | `dev/imageToImage` | POST |
| flux | `flux-dev` | `dev/textToImage` | POST |
| flux | `flux-fill-dev` | `flux-fill/generate` | POST |
| flux | `flux-pro` | `pro/textToImage` | POST |
| flux | `pro1.1` | `pro1.1ultra/generateRequest` | POST |
| gpt-image | `gpt-image-1-5-api-923` | `gpt-image-1-5-api-request` | POST |
| seedream | `byteplus` | `getEditImage` | POST |
| seedream | `byteplus` | `getEditMultiImage` | POST |
| seedream | `byteplus` | `getTextToImage` | POST |
| seedream | `seedream-5-0-lite-image` | `seedream-5-0-lite-image-request` | POST |
| seedream | `seedream-5-0-lite-image` | `seedream-5-0-lite-image-request-result` | POST |
| seedream | `seedream-5-0-lite-text-to-image` | `seedream-5-0-lite-text-to-image-request` | POST |
| seedream | `seedream-5-0-lite-text-to-image` | `seedream-5-0-lite-text-to-image-request-result` | POST |
| hunyuan | `hunyuan-image-3-0-instruct` | `hunyuan-image-3-0-instruct-request` | POST |
| hunyuan | `hunyuan-image` | `hunyuan-image/generateRequest` | POST |
| hunyuan | `hunyuan3d-3-0-api-294` | `hunyuan3d-3-0-api-request` | POST |
| firered-image-edit | `firered-image-edit` | `firered-image-edit/generate` | POST |
| firered-image-edit | `firered-image-edit` | `firered-image-edit/prediction` | POST |
| p-image | `p-image-upscale` | `p-image-upscale/generate` | POST |
| p-image | `p-image` | `p-image-edit/generate` | POST |
| wan | `pixazo-wan-image-to-video-1763709522` | `pixazo-wan-image-to-video-request` | POST |
| wan | `wan-2-6-image-to-video-477` | `wan-2-6-image-to-video-request` | POST |
| wan | `wan-2-6-image-to-video-flash-api-353` | `wan-2-6-image-to-video-flash-api-request` | POST |
| wan | `wan-2-6-text-to-video-569` | `wan-2-6-text-to-video-request` | POST |
| wan | `wan-2-7-api` | `generateWan27EditImageRequest` | POST |
| wan | `wan-2-7-api` | `generateWan27TextToImageRequest` | POST |
| wan | `wan-2-7-pro-api` | `generateWan27ProEditImageRequest` | POST |
| wan | `wan-2-7-pro-api` | `generateWan27ProTextToImageRequest` | POST |
| wan | `wan-2-7-video-api` | `generateWan27VideoEditRequest` | POST |
| wan | `wan-2-7-video-api` | `generateWan27VideoStyleRequest` | POST |
| wan | `wan-i2v` | `generateImageToVideoRequest` | POST |
| wan | `wan-image-2-5` | `generateEditImage2-5Request` | POST |
| wan | `wan-image-2-5` | `generateTextToImage2-5Request` | POST |
| wan | `wan-t2i` | `generateEditImageRequest` | POST |
| wan | `wan-t2i` | `generateTextToImageRequest` | POST |
| wan | `wan-video-2-5` | `generateImageToVideo2-5Request` | POST |
| wan | `wan-video-2-5` | `generateTextToVideo2-5Request` | POST |
| wan | `wan-video` | `generateTextToVideoRequest` | POST |
| wan | `wan2.2-s2v` | `generateSpeechToVideoRequest` | POST |
| elevenlabs | `eleven-v3-alpha-954` | `eleven-v3-alpha-request` | POST |
| elevenlabs | `elevenlabs-music-api-368` | `elevenlabs-music-api-request` | POST |
| lyria | `lyria-2` | `lyria-2/generate` | POST |
| lyria | `lyria-2` | `lyria-2/prediction` | POST |
| lyria | `lyria-3-pro` | `lyria-3-pro/generate` | POST |
| fashn-virtual-try-on | `fashn-virtual-try-on` | `fashn-virtual-try-on-request` | POST |
| fashn-virtual-try-on | `glass-virtual-try-on` | `api/glass-virtual-tryon` | POST |
| ideogram v2 | `ideogramV_2` | `generate` | POST |
| ideogram v2 | `ideogramV_2` | `describe` | POST |
| ideogram v2 | `ideogramV_2` | `edit` | POST |
| ideogram v2 | `ideogramV_2` | `remix` | POST |
| ideogram turbo | `ideogramV_2_Turbo` | `generate` | POST |
| ideogram turbo | `ideogramV_2_Turbo` | `describe` | POST |
| ideogram turbo | `ideogramV_2_Turbo` | `edit` | POST |
| ideogram turbo | `ideogramV_2_Turbo` | `remix` | POST |

---

## Video Generation

### P-Video API - AI Video Generation APIs
**Page:** https://www.pixazo.ai/models/p-video


by Pruna AI

P Video is an advanced AI video generation model offering multiple generation modes including text-to-video, image-to-video, audio-conditioned, and image+audio synthesis. Integrate seamlessly via the Pixazo API.

Models Version
P Video v1
Video Generation
Video Generation
**Request Code**
**Request Parameters**
**Example Request**
**Response**
**Request Headers**
**Response Handling**
**Pricing**
#### P Video v1 Video Generation API Documentation
https://gateway.pixazo.ai/p-video/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Video Request - P Video
**Request Code**
```
POST https://gateway.pixazo.ai/p-video/v1/p-video/generate
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_API_KEY
{
"prompt": "A cat walking in a garden"
}
```
**Output**
```
{
"request_id": "p-video_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/p-video_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Video Request
| Parameter | Required | Type | Default | Description |
| prompt | Yes | string | — | Text prompt for video generation |
| image | No | string (URI) | — | Input image URL for image-to-video generation (jpg, jpeg, png, webp) |
| audio | No | string (URI) | — | Input audio URL to condition video generation (flac, mp3, wav) |
| duration | No | integer | 5 | Duration of the video in seconds (1-10). Ignored when audio is provided |
| aspect_ratio | No | string | "16:9" | Aspect ratio of the video. Ignored when image is provided |
| resolution | No | string | "720p" | Resolution: "720p", "1080p" |
| fps | No | integer | 24 | Frames per second: 24, 48 |
| draft | No | boolean | false | Draft mode — generates a lower-quality preview faster |
| prompt_upsampling | No | boolean | true | Enhance the prompt for better results |
| disable_safety_filter | No | boolean | tru e | Disable safety filter for prompts and input image |
| save_audio | No | boolean | true | Save the video with audio |
| seed | No | integer | — | Random seed for reproducible generation |
| webhook | No | string | — | Webhook URL for async notifications when generation completes |
| webhook_events_filter | No | array | — | Event types to receive (e.g. ["completed"]) |
**Example Request**
```
{
"prompt": "A cat walking gracefully through a sunlit garden with butterflies and flowers swaying in the breeze",
"image": "https://example.com/cat.jpg",
"audio": "https://example.com/garden-ambience.wav",
"duration": 10,
"aspect_ratio": "16:9",
"resolution": "1080p",
"fps": 48,
"draft": false,
"prompt_upsampling": true,
"disable_safety_filter": true,
"save_audio": true,
"seed": 42,
"webhook": "https://your-webhook.com/callback",
"webhook_events_filter": ["completed"]
}
```
**Response**
```
{
"request_id": "p-video_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/p-video_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_API_KEY
```
Video Status - P Video
**Request Code**
```
POST https://gateway.pixazo.ai/p-video/v1/p-video/prediction
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_API_KEY
{
"requestId": "12gkggc805rmw0cwmg9skrgcb8"
}
```
**Output**
```
{
"success": true,
"id": "12gkggc805rmw0cwmg9skrgcb8",
"status": "succeeded",
"output": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/..."
}
```
Request Parameters - Video Status
| Parameter | Required | Type | Description |
| requestId | Yes | string | The prediction ID returned from the generate endpoint |
**Example Request**
```
{
"requestId": "12gkggc805rmw0cwmg9skrgcb8"
}
```
**Response**
```
{
"success": true,
"id": "12gkggc805rmw0cwmg9skrgcb8",
"status": "succeeded",
"input": {
"prompt": "A cat walking in a garden",
"aspect_ratio": "16:9",
"resolution": "720p",
"fps": 24,
"duration": 5,
"prompt_upsampling": true,
"disable_safety_filter": true,
"save_audio": true
},
"output": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/p-video/12gkggc805rmw0cwmg9skrgcb8_output_0.mp4",
"created_at": "2026-02-28T11:47:49.377Z"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_API_KEY
```

---

### Seedance 2.0 API, Seedance 1.0 Pro API, 1.0 Lite API - AI Video Generation APIs
**Page:** https://www.pixazo.ai/models/seedance


by BytePlus

Seedance 2.0 API by ByteDance offers professional AI video generation with Lite and Pro variants optimized for different quality and speed requirements. Through Pixazo's API, developers can generate videos from images and text with ByteDance's advanced motion synthesis technology. The API includes specialized features like OmniHuman for realistic human animation, making it ideal for social content and marketing videos.

Models Version
Seedance 2.0 Fast
Seedance 2.0
Seedance 1.0 Pro
Seedance 1.0 Lite
Text to Video
Image + Video + Audio to Video
Video to Video
Text to Video
**Request Code**
**Request Parameters**
**Example Request**
**Response**
**Request Headers**
**Response Handling**
**Pricing**
Image + Video + Audio to Video
Video to Video
#### Seedance 2.0 Fast Text to Video API Documentation
https://gateway.pixazo.ai/seedance-2-0-fast/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Text to Video - Seedance 2.0 Fast API
**Request Code**
```
POST https://gateway.pixazo.ai/seedance-2-0-fast/v1/text-to-video-fast
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"content": [{"type": "text", "text": "A cat walking on the beach"}]
}
```
**Output**
```
{
"request_id": "seedance-2-0-fast_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/seedance-2-0-fast_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Text to Video Fast
| Parameter | Required | Type | Description |
| content | Yes | array | Array of content items. Each item: {"type":"text","text":"your prompt"} |
| duration | No | integer | Default: 5. Video length in seconds (4-15). Use -1 for auto. |
| ratio | No | string | Default: "adaptive". Aspect ratio: "16:9", "4:3", "1:1", "3:4", "9:16", "21:9", "adaptive" |
| resolution | No | string | Default: "720p". Video resolution: "480p", "720p" |
| generate_audio | No | boolean | Default: true. Auto-generate audio for the video |
| tools | No | array | Optional tools. Example: [{"type":"web_search"}] |
| watermark | No | boolean | Default: false. Add watermark to video |
**Example Request**
```
{
"content": [{"type": "text", "text": "A cinematic shot of a cat on a tropical beach at golden hour"}],
"generate_audio": true,
"ratio": "16:9",
"duration": 15,
"resolution": "720p",
"tools": [{"type": "web_search"}],
"watermark": false
}
```
**Response**
```
{
"request_id": "seedance-2-0-fast_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/seedance-2-0-fast_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```
#### Seedance 2.0 Fast Image + Video + Audio to Video API Documentation
https://gateway.pixazo.ai/seedance-2-0-fast/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Reference to Video - Seedance 2.0 Fast API
**Request Code**
```
POST https://gateway.pixazo.ai/seedance-2-0-fast/v1/reference-to-video-fast
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"content": [
{"type": "image_url", "image_url": {"url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/f1.png"}}
]
}
```
**Output**
```
{
"request_id": "seedance-2-0-fast_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/seedance-2-0-fast_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Reference to Video Fast
| Parameter | Required | Type | Description |
| content | Yes | array | Array of content items. Must contain at least one image_url, video_url, or audio_url item. Can also include text items for prompts. |
| duration | No | integer | Default: 5. Video length in seconds (4-15). Use -1 for auto. |
| ratio | No | string | Default: "adaptive". Aspect ratio: "16:9", "4:3", "1:1", "3:4", "9:16", "21:9", "adaptive" |
| resolution | No | string | Default: "720p". Video resolution: "480p", "720p" |
| generate_audio | No | boolean | Default: true. Auto-generate audio for the video |
| watermark | No | boolean | Default: false. Add watermark to video |
Content Item Types
| Type | Format | Description |
| text | {"type":"text","text":"..."} | Text prompt or description |
| image_url | {"type":"image_url","image_url":{"url":"https://..."}} | Reference image |
| video_url | {"type":"video_url","video_url":{"url":"https://..."}} | Reference video |
| audio_url | {"type":"audio_url","audio_url":{"url":"https://..."}} | Reference audio |
**Example Request**
```
{
"content": [
{"type": "text", "text": "Animate this scene with dramatic camera movement"},
{"type": "image_url", "image_url": {"url": "https://example.com/photo.jpg"}},
{"type": "video_url", "video_url": {"url": "https://example.com/reference.mp4"}},
{"type": "audio_url", "audio_url": {"url": "https://example.com/music.mp3"}}
],
"generate_audio": false,
"ratio": "21:9",
"duration": 15,
"resolution": "720p",
"watermark": false
}
```
**Response**
```
{
"request_id": "seedance-2-0-fast_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/seedance-2-0-fast_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```
#### Seedance 2.0 Fast Video to Video API Documentation
https://gateway.pixazo.ai/seedance-2-0-fast/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Edit Video - Seedance 2.0 Fast API
**Request Code**
```
POST https://gateway.pixazo.ai/seedance-2-0-fast/v1/edit-video-fast
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"content": [
{"type": "text", "text": "Make truck Orange"},
{"type": "video_url", "video_url": {"url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/motion.mp4"}}
]
}
```
**Output**
```
{
"request_id": "seedance-2-0-fast_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/seedance-2-0-fast_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Edit Video Fast
| Parameter | Required | Type | Description |
| content | Yes | array | Array of content items. Must contain at least one video_url item (the video to edit). Can include text for edit instructions and image_url for style reference. |
| duration | No | integer | Default: 5. Video length in seconds (4-15). Use -1 for auto. |
| ratio | No | string | Default: "adaptive". Aspect ratio: "16:9", "4:3", "1:1", "3:4", "9:16", "21:9", "adaptive" |
| resolution | No | string | Default: "720p". Video resolution: "480p", "720p" |
| generate_audio | No | boolean | Default: true. Auto-generate audio for the video |
| watermark | No | boolean | Default: false. Add watermark to video |
Content Item Types
| Type | Format | Description |
| text | {"type":"text","text":"..."} | Edit instructions |
| video_url | {"type":"video_url","video_url":{"url":"https://..."}} | Video to edit (required) |
| image_url | {"type":"image_url","image_url":{"url":"https://..."}} | Style/reference image (optional) |
**Example Request**
```
{
"content": [
{"type": "text", "text": "Change background to a futuristic cityscape with neon lighting"},
{"type": "video_url", "video_url": {"url": "https://example.com/original.mp4"}},
{"type": "image_url", "image_url": {"url": "https://example.com/style-ref.jpg"}}
],
"generate_audio": true,
"ratio": "16:9",
"duration": 10,
"resolution": "720p",
"watermark": false
}
```
**Response**
```
{
"request_id": "seedance-2-0-fast_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/seedance-2-0-fast_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```
#### 2. Seedance 2.0
#### Seedance 2.0 Text to Video API Documentation
https://gateway.pixazo.ai/seedance-2-0/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Text to Video - Seedance 2.0 API
**Request Code**
```
POST https://gateway.pixazo.ai/seedance-2-0/v1/text-to-video
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"content": [{"type": "text", "text": "A cat walking on the beach"}]
}
```
**Output**
```
{
"request_id": "seedance-2-0_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/seedance-2-0_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Text to Video
| Parameter | Required | Type | Description |
| content | Yes | array | Array of content items. Each item: {"type":"text","text":"your prompt"} |
| duration | No | integer | Default: 5. Video length in seconds (4-15). Use -1 for auto. |
| ratio | No | string | Default: "adaptive". Aspect ratio: "16:9", "4:3", "1:1", "3:4", "9:16", "21:9", "adaptive" |
| resolution | No | string | Default: "720p". Video resolution: "480p", "720p" |
| generate_audio | No | boolean | Default: true. Auto-generate audio for the video |
| tools | No | array | Optional tools. Example: [{"type":"web_search"}] |
| watermark | No | boolean | Default: false. Add watermark to video |
**Example Request**
```
{
"content": [{"type": "text", "text": "A cinematic shot of a cat on a tropical beach at golden hour"}],
"generate_audio": true,
"ratio": "16:9",
"duration": 15,
"resolution": "720p",
"tools": [{"type": "web_search"}],
"watermark": false
}
```
**Response**
```
{
"request_id": "seedance-2-0_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/seedance-2-0_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```
#### Seedance 2.0 Image + Video + Audio to Video API Documentation
https://gateway.pixazo.ai/seedance-2-0/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Reference to Video - Seedance 2.0 API
**Request Code**
```
POST https://gateway.pixazo.ai/seedance-2-0/v1/reference-to-video
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"content": [
{"type": "image_url", "image_url": {"url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/f1.png"}}
]
}
```
**Output**
```
{
"request_id": "seedance-2-0_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/seedance-2-0_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Reference to Video
| Parameter | Required | Type | Description |
| content | Yes | array | Array of content items. Must contain at least one image_url, video_url, or audio_url item. Can also include text items for prompts. |
| duration | No | integer | Default: 5. Video length in seconds (4-15). Use -1 for auto. |
| ratio | No | string | Default: "adaptive". Aspect ratio: "16:9", "4:3", "1:1", "3:4", "9:16", "21:9", "adaptive" |
| resolution | No | string | Default: "720p". Video resolution: "480p", "720p" |
| generate_audio | No | boolean | Default: true. Auto-generate audio for the video |
| watermark | No | boolean | Default: false. Add watermark to video |
Content Item Types
| Type | Format | Description |
| text | {"type":"text","text":"..."} | Text prompt or description |
| image_url | {"type":"image_url","image_url":{"url":"https://..."}} | Reference image |
| video_url | {"type":"video_url","video_url":{"url":"https://..."}} | Reference video |
| audio_url | {"type":"audio_url","audio_url":{"url":"https://..."}} | Reference audio |
**Example Request**
```
{
"content": [
{"type": "text", "text": "Animate this scene with dramatic camera movement"},
{"type": "image_url", "image_url": {"url": "https://example.com/photo.jpg"}},
{"type": "video_url", "video_url": {"url": "https://example.com/reference.mp4"}},
{"type": "audio_url", "audio_url": {"url": "https://example.com/music.mp3"}}
],
"generate_audio": false,
"ratio": "21:9",
"duration": 15,
"resolution": "720p",
"watermark": false
}
```
**Response**
```
{
"request_id": "seedance-2-0_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/seedance-2-0_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```
#### Seedance 2.0 Video to Video API Documentation
https://gateway.pixazo.ai/seedance-2-0/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Edit Video - Seedance 2.0 API
**Request Code**
```
POST https://gateway.pixazo.ai/seedance-2-0/v1/edit-video
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"content": [
{"type": "text", "text": "Make truck Orange"},
{"type": "video_url", "video_url": {"url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/motion.mp4"}}
]
}
```
**Output**
```
{
"request_id": "seedance-2-0_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/seedance-2-0_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Edit Video
| Parameter | Required | Type | Description |
| content | Yes | array | Array of content items. Must contain at least one video_url item (the video to edit). Can include text for edit instructions and image_url for style reference. |
| duration | No | integer | Default: 5. Video length in seconds (4-15). Use -1 for auto. |
| ratio | No | string | Default: "adaptive". Aspect ratio: "16:9", "4:3", "1:1", "3:4", "9:16", "21:9", "adaptive" |
| resolution | No | string | Default: "720p". Video resolution: "480p", "720p" |
| generate_audio | No | boolean | Default: true. Auto-generate audio for the video |
| watermark | No | boolean | Default: false. Add watermark to video |
Content Item Types
| Type | Format | Description |
| text | {"type":"text","text":"..."} | Edit instructions |
| video_url | {"type":"video_url","video_url":{"url":"https://..."}} | Video to edit (required) |
| image_url | {"type":"image_url","image_url":{"url":"https://..."}} | Style/reference image (optional) |
**Example Request**
```
{
"content": [
{"type": "text", "text": "Change background to a futuristic cityscape with neon lighting"},
{"type": "video_url", "video_url": {"url": "https://example.com/original.mp4"}},
{"type": "image_url", "image_url": {"url": "https://example.com/style-ref.jpg"}}
],
"generate_audio": true,
"ratio": "16:9",
"duration": 10,
"resolution": "720p",
"watermark": false
}
```
**Response**
```
{
"request_id": "seedance-2-0_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/seedance-2-0_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```
#### 3. Seedance 1.0 Pro
#### Seedance 1.0 Pro Image to Video API Documentation
https://gateway.pixazo.ai/byteplus/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Seedance 1.0 Image to Video - Bytedance API
**Request Code**
```
POST https://gateway.pixazo.ai/byteplus/v1/generateImage2VideoTask
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"model": "seedance-1-0-lite-i2v-250428",
"content": [
{
"type": "text",
"text": "Soft cotton-like clouds drift with subtle layered motions across a pale blue sky. --ratio 16:9 --resolution 720p --duration 5"
},
{
"type": "image_url",
"image_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/f1.png"
}
]
}
```
**Output**
```
{
"request_id": "byteplus_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/byteplus_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Seedance 1.0 Image to Video - Bytedance API
| Parameter | Required | Type | Default | Description |
| model | Yes | string | — | The Seedance model ID. Use "seedance-1-0-lite-i2v-250428" for image-to-video. |
| content | Yes | array | — | Array of content blocks. Must include one text block (prompt with optional inline knobs) and one image_url block (source image URL). |
| content[].type | Yes | string | — | Block type: "text" or "image_url". |
| content[].text | For text block | string | — | Prompt describing the motion. Control knobs can be embedded: --ratio, --resolution, --duration, --camerafixed. |
| content[].image_url | For image_url block | string | — | Publicly accessible HTTPS URL of the source image. |
Inline knobs (embedded in text block):
| --ratio | No | string | 16:9 | Aspect ratio: 16:9, 9:16, 1:1, 4:3, 3:4, adaptive. |
| --resolution | No | string | 720p | Output resolution: 480p, 720p, 1080p. |
| --duration | No | number | 5 | Video duration in seconds (3–12). |
| --camerafixed | No | boolean | false | Lock camera position. |
**Example Request**
```
{
"model": "seedance-1-0-lite-i2v-250428",
"content": [
{
"type": "text",
"text": "Soft cotton-like clouds drift with subtle layered motions across a pale blue sky."
},
{
"type": "image_url",
"image_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/f1.png"
}
]
}
```
**Response**
```
{
"request_id": "byteplus_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/byteplus_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
X-Webhook-URL	Optional callback URL
```
#### Seedance 1.0 Pro Text to Video API Documentation
https://gateway.pixazo.ai/byteplus/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Seedance 1.0 Pro Text to Video - Bytedance API
**Request Code**
```
POST https://gateway.pixazo.ai/byteplus/v1/generateVideoTask
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"model": "seedance-1-0-lite-t2v-250428",
"text": "A vast expanse of white daisy fields under a clear blue sky. --ratio 16:9 --resolution 720p --duration 5 --camerafixed false"
}
```
**Output**
```
{
"request_id": "byteplus_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/byteplus_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Seedance 1.0 Pro Text to Video - Bytedance API
| Parameter | Required | Type | Default | Description |
| model | Yes | string | — | The Seedance model ID. Use "seedance-1-0-lite-t2v-250428" for text-to-video generation. |
| text | Yes | string | — | Prompt describing the video. Control knobs can be embedded inline: --ratio, --resolution, --duration, --camerafixed. Abbreviations: --rt, --rs, --dur, --cf. |
Inline knobs (embedded in the text field):
| --ratio | No | string | 16:9 | Aspect ratio: 16:9, 9:16, 1:1, 4:3, 3:4, 21:9, adaptive. Abbreviation: --rt. |
| --resolution | No | string | 720p (lite) / 1080p (pro) | Output resolution: 480p, 720p, 1080p. Higher = more cost and longer processing. Abbreviation: --rs. |
| --duration | No | number | 5 | Video duration in seconds. Valid range: 3–12. Abbreviation: --dur. |
| --camerafixed | No | boolean | false | Lock camera position (adds instruction to prompt). Abbreviation: --cf. |
**Example Request**
```
{
"model": "seedance-1-0-lite-t2v-250428",
"text": "A vast expanse of white daisy fields under a clear blue sky. --ratio 16:9 --resolution 720p --duration 5 --camerafixed false"
}
```
**Response**
```
{
"request_id": "byteplus_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/byteplus_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
X-Webhook-URL	Optional callback URL
```
#### 4. Seedance 1.0 Lite
#### Seedance 1.0 Lite Frame to Video API Documentation
https://gateway.pixazo.ai/byteplus/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Seedance 1.0 Lite Frame to Video - Bytedance API
**Request Code**
```
POST https://gateway.pixazo.ai/byteplus/v1/generateFrame2VideoTask
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"model": "seedance-1-0-lite-i2v-250428",
"text": "Realistic style. Aeroplane from takeoff to fly captured in camera --ratio 16:9 --resolution 720p --duration 5",
"first_frame": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/wan-t2i/wan-t2i-75d44f7d-a954-46b0-a603-10c09cb5df84-0.png",
"last_frame": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/wan-t2i/wan-t2i-47c06b16-ce7f-4977-8f7c-a04384409934-0.png"
}
```
**Output**
```
{
"request_id": "byteplus_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/byteplus_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Seedance 1.0 Lite Frame to Video - Bytedance API
| Parameter | Required | Type | Default | Description |
| model | Yes | string | — | The Seedance model ID. Use "seedance-1-0-lite-i2v-250428" for frame-to-video. |
| text | Yes | string | — | Prompt describing the motion between frames. Control knobs can be embedded: --ratio, --resolution, --duration, --camerafixed. |
| first_frame | Yes | string | — | Publicly accessible HTTPS URL of the starting frame image. |
| last_frame | Yes | string | — | Publicly accessible HTTPS URL of the ending frame image. |
Inline knobs (embedded in the text field):
| --ratio | No | string | 16:9 | Aspect ratio: 16:9, 9:16, 1:1, 4:3, 3:4. |
| --resolution | No | string | 720p | Output resolution: 480p, 720p, 1080p. |
| --duration | No | number | 5 | Video duration in seconds (3–12). |
| --camerafixed | No | boolean | false | Lock camera position. |
**Example Request**
```
{
"model": "seedance-1-0-lite-i2v-250428",
"text": "Realistic style. Aeroplane from takeoff to fly captured in camera",
"first_frame": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/wan-t2i/wan-t2i-75d44f7d-a954-46b0-a603-10c09cb5df84-0.png",
"last_frame": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/wan-t2i/wan-t2i-47c06b16-ce7f-4977-8f7c-a04384409934-0.png"
}
```
**Response**
```
{
"request_id": "byteplus_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/byteplus_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
X-Webhook-URL	Optional callback URL
```

---

### Sora 2 Pro API - AI Video Generation APIs
**Page:** https://www.pixazo.ai/models/sora


by OpenAI

Sora 2 Pro API, developers can access Sora 2 Pro for generating videos that were previously impossible with AI. The API supports both text-to-video and image-to-video generation, opening new possibilities for filmmaking, simulation, and creative expression.

Models Version
Sora 2 Pro
Image To Video
Text To Video
Image To Video
**Request Code**
**Request Parameters**
**Example Request**
**Response**
**Request Headers**
**Response Handling**
**Pricing**
Text To Video
#### Sora 2 Pro Image To Video API Documentation
https://gateway.pixazo.ai/sora-video/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Generate Image to Video Request - Sora Video API
**Request Code**
```
POST https://gateway.pixazo.ai/sora-video/v1/video/i2v/generate
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"prompt": "The woman turns her head and smiles",
"image": "https://example.com/image-1280x720.jpg"
}
```
**Output**
```
{
"request_id": "sora-video_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/sora-video_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Generate Image to Video Request
| Parameter | Required | Type | Description |
| prompt | Yes | string | The text description of how you want to animate the image. Describe the motion, action, or transformation you want to see. |
| image | Yes | string | The input image URL (http:// or https://) or base64-encoded string (e.g., data:image/jpeg;base64,...). Must be exactly one of these dimensions: 1280×720, 720×1280, 1792×1024, or 1024×1792. Images in other sizes must be resized before uploading — the API will reject unsupported dimensions with a 400 error. |
| model | No | string | The ID of the Sora model to use for generation. Currently supports sora-2-pro. |
| size | No | string | The resolution of the output video. Must match the input image dimensions exactly. If omitted, derived from the input image. Valid values: 1280x720, 720x1280, 1792x1024, 1024x1792. |
| seconds | No | integer | The duration of the output video in seconds. Valid values: 4, 8, or 12. |
| callback_url | No | string | Callback notification URL for the result of this generation task. When the video generation completes, the system sends a POST request with the video details. If omitted, use polling to retrieve results. |
**Example Request**
```
{
"prompt": "The cityscape comes alive as the sun sets, lights gradually turning on across the buildings, traffic moving below",
"image": "https://example.com/city-skyline-1792x1024.jpg",
"size": "1792x1024",
"seconds": 12,
"callback_url": "https://your-domain.com/webhook/notify"
}
```
**Response**
```
{
"request_id": "sora-video_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/sora-video_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```
Check Video Result - Sora Video API
**Request Code**
```
POST https://gateway.pixazo.ai/sora-video/v1/video/result
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"video_id": "video_68e4ff15fd8c8193ae7c27ca15812XXXXXXXXXXXXXXXXXXX"
}
```
**Output**
```
{
"id": "video_68e4ff15fd8c...",
"status": "completed",
"video_url": "https://...XXXXXXXXXXXXXXXXXXXXX.mp4"
}
```
Request Parameters - Check Video Result
| Parameter | Required | Type | Description |
| video_id | Yes | string | The unique identifier of the video generation task to check. |
**Example Request**
```
{
"video_id": "video_68e4ff15fd8c8193ae7c27ca15812XXXXXXXXXXXXXXXXXXX"
}
```
**Response**
```
{
"id": "video_68e4ff15fd8c8193ae7c27ca15812XXXXXXXXXXXXXXXXXXX",
"status": "completed",
"model": "sora-2-pro",
"prompt": "She turns around and smiles, then slowly walks out of the frame",
"created_at": 1759837181637,
"completed_at": 1759837336742,
"video_url": "https://...XXXXXXXXXXXXXXXXXXXXX.mp4",
"metadata": {
"total_attempts": 5,
"polling_duration_seconds": 155
}
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```
#### Sora 2 Pro Text To Video API Documentation
https://gateway.pixazo.ai/sora-video/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Generate Text to Video Request - Sora Video API
**Request Code**
```
POST https://gateway.pixazo.ai/sora-video/v1/video/generate
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"prompt": "A serene beach at sunset with gentle waves rolling onto the shore, palm trees swaying in the breeze, and seagulls flying overhead",
"size": "1280x720",
"seconds": 4,
"callback_url": "https://your-domain.com/webhook/notify"
}
```
**Output**
```
{
"request_id": "sora-video_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/sora-video_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Generate Text to Video Request
| Parameter | Required | Type | Description |
| prompt | Yes | string | The text description of the video you want to generate. Be descriptive and specific for best results. |
| model | No | string | The ID of the Sora model to use for generation. Currently supports `sora-2-pro`. |
| size | No | string | The resolution of the output video. Valid values: `1280x720`, `720x1280`, `1792x1024`, `1024x1792`. |
| seconds | No | integer | The duration of the output video in seconds. Valid values: `4`, `8`, or `12`. |
| callback_url | No | string | Callback notification URL for the result of this generation task. When the video generation completes, the system sends a POST request with the video details. If omitted, use polling to retrieve results. |
**Example Request**
```
{
"prompt": "A futuristic city at night with flying cars, neon lights reflecting off glass skyscrapers, and holographic advertisements floating in the air",
"size": "1792x1024",
"seconds": 12,
"callback_url": "https://your-domain.com/webhook/notify"
}
```
**Response**
```
{
"request_id": "sora-video_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/sora-video_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```

---

### Veo 3.1 Fast API Veo 3.1 API - AI Video Generation APIs
**Page:** https://www.pixazo.ai/models/veo


by Google

Veo 3.1 Fast API Veo 3.1 API, developers can access Veo 3.1 for generating videos that accurately depict motion, lighting, and real-world physics. The API leverages Google's DeepMind research to produce videos with remarkable realism, suitable for professional video production and creative applications.

Models Version
Veo v3.1 Fast
Veo v3.1
Video Generation
Video Generation
**Request Code**
**Request Parameters**
**Example Request**
**Response**
**Request Headers**
**Response Handling**
**Pricing**
#### Veo v3.1 Fast Video Generation API Documentation
https://gateway.pixazo.ai/veo31f/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Veo 3.1 Fast Video Generation Request - Veo 3.1 Fast API
**Request Code**
```
POST https://gateway.pixazo.ai/veo31f/v1/veo-3.1-fast/generate HTTP/1.1
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"prompt": "A snow-covered tree gradually transforms as winter melts away, snow dripping from branches as green leaves emerge and colorful flowers bloom around the base, transitioning from a cold white landscape to a vibrant lush green meadow full of life",
"image": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/f1.png",
"last_frame": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/f2.png",
"duration": 8,
"aspect_ratio": "16:9",
"resolution": "1080p",
"negative_prompt": "blurry, low quality, distorted, artifacts, text, watermark",
"generate_audio": true,
"seed": 42
}
```
**Output**
```
{
"request_id": "veo-3-1-fast_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/veo-3-1-fast_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Veo 3.1 Fast Video Generation
| Parameter | Required | Type | Description |
| prompt | Yes | string | Text prompt describing the desired video content, motion, mood, or style for the generated video. |
| image | No | string | Publicly accessible URL of a reference image to guide the video generation. Used as the starting frame or visual reference. |
| last_frame | No | string | Publicly accessible URL of an image to use as the last frame of the video, enabling start-to-end visual transitions. |
| duration | No | number | Duration of the generated video in seconds. Supported values: 5, 6, 8. Default varies by model configuration. |
| aspect_ratio | No | string | Aspect ratio of the output video. Supported values: "16:9", "9:16", "1:1", "4:3", "3:4". |
| resolution | No | string | Resolution of the output video. Supported values: "720p", "1080p". |
| negative_prompt | No | string | Text describing elements to avoid in the generated video (e.g., "blurry, low quality, distorted, artifacts, text, watermark"). |
| generate_audio | No | boolean | When true, generates synchronized audio alongside the video output. |
| seed | No | number | Random seed for reproducible video generation. Use the same seed to get consistent results across runs. |
**Example Request**
```
{
"prompt": "A snow-covered tree gradually transforms as winter melts away, snow dripping from branches as green leaves emerge and colorful flowers bloom around the base, transitioning from a cold white landscape to a vibrant lush green meadow full of life",
"image": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/f1.png",
"last_frame": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/f2.png",
"duration": 8,
"aspect_ratio": "16:9",
"resolution": "1080p",
"negative_prompt": "blurry, low quality, distorted, artifacts, text, watermark",
"generate_audio": true,
"seed": 42
}
```
**Response**
```
{
"request_id": "veo-3-1-fast_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/veo-3-1-fast_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```
#### 2. Veo v3.1
#### Veo v3.1 Video Generation API Documentation
https://gateway.pixazo.ai/veo/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Video Generation Request - Veo 3.1 API
**Request Code**
```
POST https://gateway.pixazo.ai/veo/v1/veo-3.1/generate
Content-Type: application/json
Ocp-Apim-Subscription-Key: your-subscription-key
{
"prompt": "A serene lake with mountains in the background at sunset",
"aspect_ratio": "16:9",
"duration": 8,
"resolution": "1080p",
"generate_audio": true,
"negative_prompt": "blur, distortion, low quality",
"webhook": "https://your-server.com/webhook"
}
```
**Output**
```
{
"request_id": "veo-3-1_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/veo-3-1_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Video Generation Request
| Parameter | Required | Type | Description |
| prompt | Yes | string | Text description of the video to generate (required) |
| aspect_ratio | No | string | Video aspect ratio: "16:9" or "9:16" |
| duration | No | integer | Video duration in seconds: 4, 6, or 8 |
| resolution | No | string | Video resolution: "720p" or "1080p" |
| generate_audio | No | boolean | Whether to generate audio with the video |
| negative_prompt | No | string | What to exclude from the generated video |
| image | No | string | Input image URL for image-to-video generation |
| last_frame | No | string | Ending image URL for interpolation (requires image) |
| reference_images | No | array | 1-3 reference images for subject-consistent generation (R2V) |
| seed | No | integer | Random seed for reproducible results (optional) |
| webhook | No | string | Webhook URL for completion notifications |
| webhook_events_filter | No | array | Event types to receive: ["start", "completed"] |
**Example Request**
```
{
"prompt": "A serene lake with mountains in the background at sunset",
"aspect_ratio": "16:9",
"duration": 8,
"resolution": "1080p",
"generate_audio": true,
"negative_prompt": "blur, distortion, low quality",
"webhook": "https://your-server.com/webhook"
}
```
**Response**
```
{
"request_id": "veo-3-1_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/veo-3-1_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Ocp-Apim-Subscription-Key	Your subscription key
```

---

### Runway Gen 4.5 API - AI Video Generation APIs
**Page:** https://www.pixazo.ai/models/runway


by Runway

Runway Gen 4.5 API, developers can access Runway's industry-leading video generation for creating cinematic content from text and images. The API powers professional video production workflows, offering the quality and features demanded by filmmakers, advertisers, and media companies.

Models Version
Runway Gen-4.5
Image To Video
Image To Video
**Request Code**
**Request Parameters**
**Example Request**
**Response**
**Request Headers**
**Response Handling**
**Pricing**
#### Runway Gen-4.5 Image To Video API Documentation
https://gateway.pixazo.ai/runway-gen-4-5/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Video Generation Request - Runway gen-4.5
**Request Code**
```
POST https://gateway.pixazo.ai/runway-gen-4-5/v1/gen-4.5/generate
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_API_KEY
{
"prompt": "A golden retriever puppy chasing butterflies through a sunlit lavender field, slow motion, cinematic depth of field, warm afternoon light casting long shadows"
}
```
**Output**
```
{
"request_id": "runway-gen-4-5_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/runway-gen-4-5_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Video Generation Request
| Parameter | Required | Type | Description |
| prompt | Yes | string | Text prompt describing the video to generate |
| image | No | string (URI) | Image URL for the first frame (image-to-video mode). If omitted, generates from text only |
| duration | No | integer | Video duration in seconds. Allowed values: 5, 10 |
| aspect_ratio | No | string | Video aspect ratio. Allowed values: "16:9", "9:16", "4:3", "3:4", "1:1", "21:9" |
| seed | No | integer | Random seed for reproducible generation |
**Example Request**
```
{
"prompt": "An astronaut gazes through the cupola window of a space station at the glowing curve of Earth below, soft ambient light from control panels illuminates the cabin, the camera slowly drifts forward revealing the full panoramic viewport weightless tools float gently in the background, calm and awe-inspiring cinematic atmosphere",
"image": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/iss043e284928~medium.jpg",
"duration": 10,
"aspect_ratio": "21:9",
"seed": 42
}
```
**Response**
```
{
"request_id": "runway-gen-4-5_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/runway-gen-4-5_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	Your API subscription key
```
Check Prediction Status - Runway gen-4.5
**Request Code**
```
POST https://gateway.pixazo.ai/runway-gen-4-5/v1/gen-4.5/prediction
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_API_KEY
{
"prediction_id": "phzd8zp6anrmt0cwfcntztr2xc"
}
```
**Output**
```
{
"success": true,
"id": "phzd8zp6anrmt0cwfcntztr2xc",
"status": "succeeded",
"output": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/gen-4.5/phzd8zp6anrmt0cwfcntztr2xc_output_0.mp4",
"created_at": "2026-02-20T13:09:52.341Z"
}
```
Request Parameters - Check Prediction Status
| Parameter | Required | Type | Description |
| prediction_id | Yes | string | The prediction ID returned from the generate endpoint |
**Example Request**
```
{
"prediction_id": "phzd8zp6anrmt0cwfcntztr2xc"
}
```
**Response**
```
{
"success": true,
"id": "phzd8zp6anrmt0cwfcntztr2xc",
"status": "succeeded",
"input": {
"aspect_ratio": "16:9",
"duration": 5,
"prompt": "A golden retriever puppy chasing butterflies through a sunlit lavender field..."
},
"output": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/gen-4.5/phzd8zp6anrmt0cwfcntztr2xc_output_0.mp4",
"created_at": "2026-02-20T13:09:52.341Z"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	Your API subscription key
```

---

### Kling Video 3.0 Motion Control API, Kling 3.0, 2.6, 2.0, 1.6, O1 API - AI Video Generation APIs
**Page:** https://www.pixazo.ai/models/kling


by Kuaishou

Kling Video 3.0 Motion Control API, developers can generate high-quality videos from text and images, with advanced features like motion control, video-to-video editing, and avatar generation. The API supports extended video lengths and cinematic quality output.

Models Version
Kling Image O3
Kling 3.0
Kling Image V3 t2i
Kling v2.6 Standard
Kling Avatar v2 Pro
Kling v1.6
Kling O1
Kling v1
Text to Image
Image to Image
Text to Image
**Request Code**
**Request Parameters**
**Example Request**
**Response**
**Request Headers**
**Response Handling**
**Pricing**
Image to Image
#### Kling Image O3 Text to Image API Documentation
https://gateway.pixazo.ai/kling-image/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Kling Image O3 Text to Image - Kling Image API
**Request Code**
```
POST https://gateway.pixazo.ai/kling-image/v1/kling-image-request
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"prompt": "A serene mountain lake at sunset with golden reflections, painterly style",
"resolution": "1K",
"aspect_ratio": "auto",
"num_images": 1,
"output_format": "png"
}
```
**Output**
```
{
"request_id": "kling-image_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/kling-image_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Kling Image O3 Text to Image - Kling Image API
| Parameter | Required | Type | Default | Description |
| prompt | Yes | string | — | Text description of the image to generate. Max 2500 characters. |
| resolution | No | string | 1K | Target output resolution. Supported: "1K", "2K", "4K". Higher resolutions increase cost. |
| result_type | No | string | single | Output type: "single" for one or more independent results, "series" for a coordinated sequence. |
| num_images | No | integer | 1 | Number of output images to generate (1-9). Only applicable when result_type is "single". |
| series_amount | No | integer | — | Number of images in a coordinated sequence (2-9). Only applicable when result_type is "series". |
| aspect_ratio | No | string | auto | Desired aspect ratio: "auto", "1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "21:9". |
| output_format | No | string | png | Output image format: "png", "jpeg", or "webp". |
| sync_mode | No | boolean | false | If true, returns image as data URI directly in the response instead of a URL. |
**Example Request**
```
{
"prompt": "A serene mountain lake at sunset with golden reflections"
}
```
**Response**
```
{
"request_id": "kling-image_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/kling-image_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
X-Webhook-URL	Optional callback URL
```
#### Kling Image O3 Image to Image API Documentation
https://gateway.pixazo.ai/kling-image-o3-i2i/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Kling Image O3 Image to Image - Kling Image O3 i2i API
**Request Code**
```
POST https://gateway.pixazo.ai/kling-image-o3-i2i/v1/kling-image-o3-i2i-request
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"prompt": "Reimagine this as a detailed pencil sketch with cross-hatching on aged parchment",
"image_urls": [
"https://imagesai.appypie.com/7686410/a4i4mHl7B9MUtbt09qBA_017731443271512.png"
],
"resolution": "1K",
"aspect_ratio": "auto",
"num_images": 1,
"output_format": "png"
}
```
**Output**
```
{
"request_id": "kling-image-o3-i2i_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/kling-image-o3-i2i_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Kling Image O3 Image to Image - Kling Image O3 i2i API
| Parameter | Required | Type | Default | Description |
| prompt | Yes | string | — | Text description of how to transform the input image. Max 2500 characters. |
| image_urls | Yes | array of strings | — | Array of 1-10 HTTPS URLs pointing to reference images. Multiple references can be controlled with @Image1, @Image2 syntax in the prompt. |
| elements | No | array | — | ElementInput objects for advanced face control and character consistency. |
| resolution | No | string | 1K | Target output resolution. Supported: "1K", "2K", "4K". Higher resolutions increase cost. |
| result_type | No | string | single | Output type: "single" for one or more independent results, "series" for a coordinated sequence. |
| num_images | No | integer | 1 | Number of output images to generate (1-9). Only applicable when result_type is "single". |
| series_amount | No | integer | — | Number of images in a coordinated sequence (2-9). Only applicable when result_type is "series". |
| aspect_ratio | No | string | auto | Desired aspect ratio: "auto", "1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "21:9". "auto" preserves the input image's aspect ratio. |
| output_format | No | string | png | Output image format: "png", "jpeg", or "webp". |
| sync_mode | No | boolean | false | If true, returns image as data URI directly in the response instead of a URL. |
**Example Request**
```
{
"prompt": "Reimagine this as a detailed pencil sketch with cross-hatching on aged parchment",
"image_urls": [
"https://imagesai.appypie.com/7686410/a4i4mHl7B9MUtbt09qBA_017731443271512.png"
]
}
```
**Response**
```
{
"request_id": "kling-image-o3-i2i_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/kling-image-o3-i2i_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
X-Webhook-URL	Optional callback URL
```
#### 2. Kling 3.0
#### Kling 3.0 Text To Video API Documentation
https://gateway.pixazo.ai/kling-3-0-text-to-video-standard/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Kling 3.0 Text to Video Standard generate request - Kling 3.0 Text to Video Standard
**Request Code**
```
POST https://gateway.pixazo.ai/kling-3-0-text-to-video-standard/v1/kling-3-0-text-to-video-standard-request
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"prompt": "Cinematic drone shot flying through ancient stone ruins covered in moss and vines at golden hour. Camera starts low, rises through crumbling archways, revealing a vast misty valley beyond. Volumetric light rays pierce through gaps in the stone. Epic scale, photorealistic, 8K quality.",
"duration": "5",
"multi_prompt": null,
"generate_audio": true,
"shot_type": "customize",
"aspect_ratio": "16:9",
"negative_prompt": "blur, distort, and low quality",
"cfg_scale": 0.5
}
```
**Output**
```
{
"request_id": "kling-3-0-text-to-video-standard_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/kling-3-0-text-to-video-standard_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Kling 3.0 Text to Video Standard generate request
| Parameter | Required | Type | Description |
| prompt | Yes | string | A detailed text description of the desired video scene. Be specific about camera movement, lighting, mood, and visual elements for best results. |
| duration | Yes | string | Duration of the generated video in seconds. Must be between "3" and "15". |
| multi_prompt | No | string or null | Optional secondary prompt for multi-sequence video generation. Use null for single-prompt generation. |
| generate_audio | No | boolean | Whether to generate synchronized audio with the video. Set to false to disable audio. |
| shot_type | No | string | Type of camera shot. Options: "customize", "static", "pan", "zoom", "dolly". |
| aspect_ratio | No | string | Output video aspect ratio. Options: "16:9", "9:16", "1:1". |
| negative_prompt | No | string | Describes undesired elements to avoid in the output. Helps refine visual quality. |
| cfg_scale | No | number | Classifier-free guidance scale. Controls how closely the output adheres to the prompt. Range: 0.1 to 2.0. Higher values increase prompt fidelity. |
**Example Request**
```
{
"prompt": "Cinematic drone shot flying through ancient stone ruins covered in moss and vines at golden hour. Camera starts low, rises through crumbling archways, revealing a vast misty valley beyond. Volumetric light rays pierce through gaps in the stone. Epic scale, photorealistic, 8K quality.",
"duration": "5",
"multi_prompt": null,
"generate_audio": true,
"shot_type": "customize",
"aspect_ratio": "16:9",
"negative_prompt": "blur, distort, and low quality",
"cfg_scale": 0.5
}
```
**Response**
```
{
"request_id": "kling-3-0-text-to-video-standard_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/kling-3-0-text-to-video-standard_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```
Kling 3.0 Text to Video Standard check status - Kling 3.0 Text to Video Standard
**Request Code**
```
POST https://gateway.pixazo.ai/kling-3-0-text-to-video-standard/v1/kling-3-0-text-to-video-standard-request-result
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"request_id": "abc123-def456"
}
```
**Output**
```
{
"video": {
"url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/XXXXXXXXXXXXXXXXXX.mp4",
"content_type": "video/mp4",
"file_name": "output.mp4",
"file_size": 6841082
}
}
```
Request Parameters - Kling 3.0 Text to Video Standard check status
| Parameter | Required | Type | Description |
| request_id | Yes | string | The unique identifier returned from the initial request to check its status. |
**Example Request**
```
{
"request_id": "abc123-def456"
}
```
**Response**
```
{
"video": {
"url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/XXXXXXXXXXXXXXXXXX.mp4",
"content_type": "video/mp4",
"file_name": "output.mp4",
"file_size": 6841082
}
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```
#### Kling 3.0 Image To Video API Documentation
https://gateway.pixazo.ai/kling-3-0-image-to-video-standard/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Kling 3.0 Image to Video Standard generate request - Kling 3.0 Image to Video Standard
**Request Code**
```
POST https://gateway.pixazo.ai/kling-3-0-image-to-video-standard/v1/kling-3-0-image-to-video-standard-request
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"prompt": "Create a magical timelapse transition. The snow melts rapidly to reveal green grass, and the tree branches burst into bloom with pink flowers in real-time. The lighting shifts from cold winter light to warm spring sunshine. The camera pushes in slowly towards the tree. Disney-style magical transformation, cinematic, 8k.",
"start_image_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/f1.png",
"end_image_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/f2.png",
"duration": "5",
"multi_prompt": null,
"generate_audio": true,
"shot_type": "customize",
"aspect_ratio": "16:9",
"negative_prompt": "blur, distort, and low quality",
"cfg_scale": 0.5
}
```
**Output**
```
{
"request_id": "kling-3-0-image-to-video-standard_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/kling-3-0-image-to-video-standard_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Kling 3.0 Image to Video Standard generate request
| Parameter | Required | Type | Description |
| prompt | Yes | string | Detailed text description of the desired video transformation, including motion, lighting, and style. |
| start_image_url | Yes | string | Publicly accessible URL of the starting image for the video transition. |
| end_image_url | Yes | string | Publicly accessible URL of the ending image for the video transition. |
| duration | Yes | string | Duration of the generated video in seconds. Must be between 3 and 15. |
| multi_prompt | No | string or null | Optional additional prompt for complex scene variations. Use null if not needed. |
| generate_audio | Yes | boolean | Whether to generate synchronized ambient audio matching the video motion and mood. |
| shot_type | Yes | string | Type of camera motion. Options: "customize", "pan_left", "pan_right", "zoom_in", "zoom_out", "tilt_up", "tilt_down". |
| aspect_ratio | Yes | string | Output video aspect ratio. Options: "16:9", "9:16", "1:1". |
| negative_prompt | No | string | Describes undesired elements to avoid in the output (e.g., blur, distort, low quality). |
| cfg_scale | No | number | Control the influence of the prompt on the generation. Lower values (0.1–0.5) allow more creative freedom; higher values (0.6–2.0) follow the prompt more strictly. |
**Example Request**
```
{
"prompt": "Create a magical timelapse transition. The snow melts rapidly to reveal green grass, and the tree branches burst into bloom with pink flowers in real-time. The lighting shifts from cold winter light to warm spring sunshine. The camera pushes in slowly towards the tree. Disney-style magical transformation, cinematic, 8k.",
"start_image_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/f1.png",
"end_image_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/f2.png",
"duration": "5",
"multi_prompt": null,
"generate_audio": true,
"shot_type": "customize",
"aspect_ratio": "16:9",
"negative_prompt": "blur, distort, and low quality",
"cfg_scale": 0.5
}
```
**Response**
```
{
"request_id": "kling-3-0-image-to-video-standard_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/kling-3-0-image-to-video-standard_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```
Kling 3.0 Image to Video Standard check status - Kling 3.0 Image to Video Standard
**Request Code**
```
POST https://gateway.pixazo.ai/kling-3-0-image-to-video-standard/v1/kling-3-0-image-to-video-standard-request-result
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"request_id": "abc123-def456"
}
```
**Output**

```
{
"video": {
"url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/XXXXXXXXXXXXXXXXXX.mp4",
"content_type": "video/mp4",
"file_name": "output.mp4",
"file_size": 6841082
}
}
```
Request Parameters - Kling 3.0 Image to Video Standard check status
| Parameter | Required | Type | Description |
| request_id | Yes | string | The unique identifier returned from the initial request to track status. |
**Example Request**
```
{
"request_id": "abc123-def456"
}
```
**Response**
```
{
"video": {
"url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/XXXXXXXXXXXXXXXXXX.mp4",
"content_type": "video/mp4",
"file_name": "output.mp4",
"file_size": 6841082
}
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```
#### 3. Kling Image V3 t2i
#### Kling Image V3 t2i Text To Image API Documentation
https://gateway.pixazo.ai/kling-image-t2i/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Kling Image t2i generate request - Kling Image t2i
**Request Code**
```
POST https://gateway.pixazo.ai/kling-image-t2i/v1/kling-image-t2i-request
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"prompt": "An aerial view of a lavender field in Provence at sunset, rows of purple flowers stretching to the horizon, warm golden light, countryside landscape photography",
"resolution": "1K",
"num_images": 1,
"aspect_ratio": "16:9",
"output_format": "png"
}
```
**Output**
```
{
"request_id": "kling-image-t2i_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/kling-image-t2i_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Kling Image t2i generate request
| Field | Type | Required | Default | Description |
| prompt | string | Yes | — | Text description describing the desired image. Be specific for optimal results. |
| resolution | string | No | 1K | Image resolution: "1K", "2K", or "4K". Higher resolutions require more processing time. |
| num_images | integer | No | 1 | Number of images to generate per request. Must be between 1 and 4. |
| aspect_ratio | string | No | 16:9 | Image aspect ratio. Supported values: "1:1", "4:3", "16:9", "9:16", "21:9". |
| output_format | string | No | png | Output image format. Supported values: "png", "jpeg", "webp". |
Minimum Request
```
{
"prompt": "A realistic photo of a golden retriever puppy playing in a sunlit meadow"
}
```
Full Request (all options)
```
{
"prompt": "A realistic photo of a golden retriever puppy playing in a sunlit meadow",
"resolution": "4K",
"num_images": 4,
"aspect_ratio": "1:1",
"output_format": "webp"
}
```
**Response**
```
{
"request_id": "kling-image-t2i_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/kling-image-t2i_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	Your API subscription key
```
#### Kling Image V3 t2i Text To Image API Documentation
https://gateway.pixazo.ai/kling-image-t2i/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Kling Image t2i generate request - Kling Image t2i
**Request Code**
```
POST https://gateway.pixazo.ai/kling-image-t2i/v1/kling-image-t2i-request
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"prompt": "An aerial view of a lavender field in Provence at sunset, rows of purple flowers stretching to the horizon, warm golden light, countryside landscape photography",
"resolution": "1K",
"num_images": 1,
"aspect_ratio": "16:9",
"output_format": "png"
}
```
**Output**
```
{
"request_id": "kling-image-t2i_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/kling-image-t2i_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Kling Image t2i generate request
| Field | Type | Required | Default | Description |
| prompt | string | Yes | — | Text description describing the desired image. Be specific for optimal results. |
| resolution | string | No | 1K | Image resolution: "1K", "2K", or "4K". Higher resolutions require more processing time. |
| num_images | integer | No | 1 | Number of images to generate per request. Must be between 1 and 4. |
| aspect_ratio | string | No | 16:9 | Image aspect ratio. Supported values: "1:1", "4:3", "16:9", "9:16", "21:9". |
| output_format | string | No | png | Output image format. Supported values: "png", "jpeg", "webp". |
Minimum Request
```
{
"prompt": "A realistic photo of a golden retriever puppy playing in a sunlit meadow"
}
```
Full Request (all options)
```
{
"prompt": "A realistic photo of a golden retriever puppy playing in a sunlit meadow",
"resolution": "4K",
"num_images": 4,
"aspect_ratio": "1:1",
"output_format": "webp"
}
```
**Response**
```
{
"request_id": "kling-image-t2i_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/kling-image-t2i_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	Your API subscription key
```
#### 4. Kling v2.6 Standard
#### Kling v2.6 Standard Motion Control API Documentation
https://gateway.pixazo.ai/kling-video-v2-6-standard-motion-control/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Kling Video v2.6 Standard Motion Control generate request - Kling Video v2.6 Standard Motion Control API
**Request Code**
```
POST https://gateway.pixazo.ai/kling-video-v2-6-standard-motion-control/v1/kling-video-v2-6-standard-motion-control-request
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"image_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/motion.jpg",
"video_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/motion.mp4",
"character_orientation": "video",
"keep_original_sound": true
}
```
**Output**
```
{
"request_id": "kling-video-v2-6-standard-motion-control_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/kling-video-v2-6-standard-motion-control_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Kling Video v2.6 Standard Motion Control generate request
| Parameter | Required | Type | Description |
| image_url | Yes | string | URL pointing to the static character image (JPEG/PNG) to which motion will be transferred. Must be publicly accessible. Maximum allowed size of 10485760 bytes |
| video_url | Yes | string | URL pointing to the reference video containing the motion to transfer (MP4). Must be publicly accessible and under 10 seconds. |
| character_orientation | No | string | Specifies how the character in the image should align with the video motion. Use "video" to match the video’s orientation or "image" to match the image’s orientation. |
| keep_original_sound | No | boolean | If true, preserves the audio from the reference video in the output. If false, output will be silent. |
**Example Request**
```
{
"image_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/motion.jpg",
"video_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/motion.mp4",
"character_orientation": "video",
"keep_original_sound": true
}
```
**Response**
```
{
"request_id": "kling-video-v2-6-standard-motion-control_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/kling-video-v2-6-standard-motion-control_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```
#### 5. Kling Avatar v2 Pro
#### Kling Avatar v2 Pro AI Avatar API Documentation
https://gateway.pixazo.ai/kling-ai-avatar-v2-pro-789/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Kling AI Avatar v2 Pro generate request - Kling AI Avatar v2 Pro API
**Request Code**
```
POST https://gateway.pixazo.ai/kling-ai-avatar-v2-pro-789/v1/kling-ai-avatar-v2-pro-request
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"image_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/input_model.png",
"audio_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/abhinav_YF5ZZmi6.mp3",
"prompt": "smiling warmly, slight head turn"
}
```
**Output**
```
{
"request_id": "kling-ai-avatar-v2-pro-789_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/kling-ai-avatar-v2-pro-789_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Kling AI Avatar v2 Pro generate request
| Field | Type | Required | Default | Description |
| image_url | string | Yes | — | Publicly accessible URL to a static image (face portrait) to be animated. Supported formats: JPEG, PNG. |
| audio_url | string | Yes | — | Publicly accessible URL to an audio file (MP3, WAV) to drive lip-sync and expression animation. Maximum duration: 60 seconds. |
| prompt | string | No | ." | Additional descriptive prompt to guide avatar expression and motion style. Example: "smiling warmly, slight head turn". |
Minimum Request
```
{
"image_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/input_model.png",
"audio_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/abhinav_YF5ZZmi6.mp3"
}
```
Full Request (all options)
```
{
"image_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/input_model.png",
"audio_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/abhinav_YF5ZZmi6.mp3",
"prompt": "smiling warmly, slight head turn"
}
```
**Response**
```
{
"request_id": "kling-ai-avatar-v2-pro-789_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/kling-ai-avatar-v2-pro-789_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	Your API subscription key
```
#### 6. Kling v1.6
#### Kling v1.6 Image To Video API Documentation
https://gateway.pixazo.ai/kling-ai-video/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Image To Video Request - klingai Video API
**Request Code**
```
POST https://gateway.pixazo.ai/kling-ai-video/v1/getImageToVideoTask
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"prompt": "Bird fishing in water",
"image": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/b-d-a-lbdo-2-5-21.jpg",
"negative_prompt": "blur, distort, and low quality"
}
```
**Output**
```
{
"request_id": "klingai-video_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/klingai-video_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Image To Video Request
| Parameter | Required | Type | Description |
| image | Yes | string | Support inputting image Base64 encoding or image URL (ensure accessibility). Ex: https://pub-582b7213209642b9b995c96c95a30381.r2.dev/b-d-a-lbdo-2-5-21.jpg. Please note, if you use the Base64 method, make sure all image data parameters you pass are in Base64 encoding format. When submitting data, do not add any prefixes to the Base64-encoded string, such as data:image/png;base64. The correct parameter format should be the Base64-encoded string itself. |
| prompt | Yes | string | Default: null, The instruction or description for the video scene to be generated. Cannot exceed 2500 characters |
| negative_prompt | Optional | string | Negative text prompt. Cannot exceed 2500 characters |
| duration | Optional | string | The duration of the generated video in seconds Default value: "5". Possible enum values: 5, 10 |
| cfg_scale | Optional | float | Default 0.5, Value range: [0, 1]. Flexibility in video generation; The higher the value, the lower the model's degree of flexibility, and the stronger the relevance to the user's prompt |
**Example Request**
```
{
"prompt": "Bird fishing in water",
"image": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/b-d-a-lbdo-2-5-21.jpg",
"negative_prompt": "blur, distort, and low quality"
}
```
**Response**
```
{
"request_id": "klingai-video_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/klingai-video_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```
#### Kling v1.6 Text To Video API Documentation
https://gateway.pixazo.ai/kling-ai-video/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Text To Video Request - klingai Video API
**Request Code**
```
POST https://gateway.pixazo.ai/kling-ai-video/v1/generateVideoTask
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"prompt": "An enchanted forest with glowing mushrooms, fireflies, and a sparkling river flowing through the trees.",
"negative_prompt": "nude, porn, abusive"
}
```
**Output**
```
{
"request_id": "klingai-video_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/klingai-video_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Text To Video Request
| Parameter | Required | Type | Description |
| prompt | Yes | string | The instruction or description for the video scene to be generated. Cannot exceed 2500 characters |
| negative_prompt | No | string | Negative text prompt. Cannot exceed 2500 characters |
| duration | Optional | string | The duration of the generated video in seconds Default value: "5". Possible enum values: 5, 10 |
| cfg_scale | No | float | Default 0.5, Value range: [0, 1]. Flexibility in video generation; The higher the value, the lower the model's degree of flexibility, and the stronger the relevance to the user's prompt |
| aspect_ratio | No | string | Default: 16:9. The aspect ratio of the generated video frame (width:height). Enum values：16:9, 9:16, 1:1 |
**Example Request**
```
{
"prompt": "An enchanted forest with glowing mushrooms, fireflies, and a sparkling river flowing through the trees.",
"negative_prompt": "nude, porn, abusive"
}
```
**Response**
```
{
"request_id": "klingai-video_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/klingai-video_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```
#### 7. Kling O1
#### Kling O1 Edit Video API Documentation
https://gateway.pixazo.ai/kling-o1-edit-video-video-to-video-634/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Generate Request - Kling O1 Edit Video API
**Request Code**
```
POST https://gateway.pixazo.ai/kling-o1-edit-video-video-to-video-634/v1/kling-o1-edit-video-video-to-video-request
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"prompt": "Replace the character in the video with @Element1, maintaining the same movements and camera angles. Transform the landscape into @Image1",
"video_url": "https://example.com/input-video.mp4"
}
```
**Output**
```
{
"request_id": "kling-o1-edit-video-video-to-video-634_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/kling-o1-edit-video-video-to-video-634_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Generate Request
| Field | Type | Required | Default | Description |
| prompt | string | Yes | — | Natural language instruction describing the desired video transformation. Use placeholders like @Element1 and @Image1 to reference elements and images defined in the payload. |
| video_url | string | Yes | — | Publicly accessible URL of the input video to be edited. Must be accessible without authentication. Reference video URL. Only .mp4/.mov formats supported, 3-10 seconds duration, 720-2160px resolution, max 200MB. Min width: 720px, Min height: 720px, Max width: 2160px, Max height: 2160px, Min duration: 3.0s, Max duration: 10.05s, Min FPS: 24.0, Max FPS: 60.0, Timeout: 30.0s |
| image_urls | array of strings | No | [] | Array of image URLs to be used as background or environment replacements in the prompt (referenced as @Image1, @Image2, etc.). |
| elements | array of objects | No | [] | Array of element definitions containing reference and frontal images for subject replacement. Each element is referenced in the prompt via @Element1, @Element2, etc. |
Minimum Request
```
{
"prompt": "Replace tree with a Mango Tree with flower. All purpul flower color should change to yellow",
"video_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/kling-o1-reference-image-to-video-382/zLDbsHoe_0jS8ClKdo9P__output.mp4"
}
```
Full Request (all options)
```
{
"prompt": "Replace the character in the video with @Element1, maintaining the same movements and camera angles. Transform the landscape into @Image1",
"video_url": "https://example.com/input-video.mp4",
"image_urls": [
"https://example.com/background.jpg"
],
"elements": [
{
"reference_image_urls": [
"https://example.com/reference1.jpg",
"https://example.com/reference2.jpg"
],
"frontal_image_url": "https://example.com/frontal.jpg"
}
]
}
```
**Response**
```
{
"request_id": "kling-o1-edit-video-video-to-video-634_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/kling-o1-edit-video-video-to-video-634_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	Your API subscription key
```
#### Kling O1 Frame To Video API Documentation
https://gateway.pixazo.ai/kling-o1-first-frame-last-frame-to-video-857/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Generate Request - Kling O1 First Frame Last Frame to Video API
**Request Code**
```
POST https://gateway.pixazo.ai/kling-o1-first-frame-last-frame-to-video-857/v1/kling-o1-first-frame-last-frame-to-video-request
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"prompt": "Create a magical timelapse transition. The snow melts rapidly to reveal green grass, and the tree branches burst into bloom with pink flowers in real-time. The lighting shifts from cold winter light to warm spring sunshine. The camera pushes in slowly towards the tree. Disney-style magical transformation, cinematic, 8k.",
"start_image_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/f1.png",
"end_image_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/f2.png",
"duration": "5"
}
```
**Output**
```
{
"request_id": "kling-o1-first-frame-last-frame-to-video-857_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/kling-o1-first-frame-last-frame-to-video-857_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Generate Request
| Parameter | Required | Type | Description |
| prompt | Yes | string | A detailed natural language description of the visual transformation you want to generate. Include motion, lighting, style, and mood. |
| start_image_url | Yes | string | Publicly accessible URL to the JPG or PNG image that represents the starting frame of the video. |
| end_image_url | Yes | string | Publicly accessible URL to the JPG or PNG image that represents the ending frame of the video. |
| duration | Optional | string | Duration of the resulting video in seconds. Must be a numeric string (e.g., "5", "10"). |
**Example Request**
```
{
"prompt": "Create a magical timelapse transition. The snow melts rapidly to reveal green grass, and the tree branches burst into bloom with pink flowers in real-time. The lighting shifts from cold winter light to warm spring sunshine. The camera pushes in slowly towards the tree. Disney-style magical transformation, cinematic, 8k.",
"start_image_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/f1.png",
"end_image_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/f2.png",
"duration": "5"
}
```
**Response**
```
{
"request_id": "kling-o1-first-frame-last-frame-to-video-857_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/kling-o1-first-frame-last-frame-to-video-857_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```
#### Kling O1 Image To Image API Documentation
https://gateway.pixazo.ai/kling-o1-image-208/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Generate Request - Kling O1 Image API
**Request Code**
```
POST https://gateway.pixazo.ai/kling-o1-image-208/v1/kling-o1-image-request
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"prompt": "@image1 is the model and @image2 is the glasses. Perform a virtual try-on by placing the glasses from @image2 onto the model in @image1.",
"image_urls": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/Image.jpeg",
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/Captura-de-tela-2025-07-17%20171002.png"
],
"resolution": "1K",
"num_images": 1,
"aspect_ratio": "auto",
"output_format": "png"
}
```
**Output**
```
{
"request_id": "kling-o1-image-208_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/kling-o1-image-208_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Generate Request
| Parameter | Required | Type | Description |
| prompt | Yes | string | A detailed textual instruction describing the transformation. Must reference images using `@image1` and `@image2` to indicate subject and source. Example: `"@image1 is the model and @image2 is the glasses. Perform a virtual try-on by placing the glasses from @image2 onto the model in @image1."` |
| image_urls | Yes | array of strings | Array of two URLs pointing to the source images. `@image1` corresponds to the first URL (subject), `@image2` to the second URL (reference object). |
| resolution | No | string | Target resolution of the output image. Accepted values: `"1K"`, `"2K"`, `"4K"`. |
| num_images | No | integer | Number of output images to generate. Supported values: 1–4. |
| aspect_ratio | No | string | Desired aspect ratio for the output. Accepted values: `"auto"`, `"1:1"`, `"4:3"`, `"16:9"`, `"9:16"`. `"auto"` adapts to input image dimensions. |
| output_format | No | string | Format of the generated image output. Accepted values: `"png"`, `"jpeg"`. |
**Example Request**
```
{
"prompt": "@image1 is the model and @image2 is the glasses. Perform a virtual try-on by placing the glasses from @image2 onto the model in @image1.",
"image_urls": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/Image.jpeg",
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/Captura-de-tela-2025-07-17%20171002.png"
],
"resolution": "1K",
"num_images": 1,
"aspect_ratio": "auto",
"output_format": "png"
}
```
**Response**
```
{
"request_id": "kling-o1-image-208_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/kling-o1-image-208_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```
#### Kling O1 Reference Image To Video API Documentation
https://gateway.pixazo.ai/kling-o1-reference-image-to-video-382/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Generate Request - Kling O1 Reference Image to Video API
**Request Code**
```
POST https://gateway.pixazo.ai/kling-o1-reference-image-to-video-382/v1/kling-o1-reference-image-to-video-request
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"prompt": "Take @Image1 as the start frame. Create a smooth cinematic transition that matches the style and composition of @Image2. The camera should move elegantly, maintaining visual consistency between the two reference images. Cinematic lighting, professional quality, 35mm lens.",
"image_urls": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/f1.png",
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/f2.png"
],
"duration": "5",
"aspect_ratio": "16:9"
}
```
**Output**
```
{
"request_id": "kling-o1-reference-image-to-video-382_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/kling-o1-reference-image-to-video-382_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Generate Request
| Parameter | Required | Type | Description |
| prompt | Yes | string | Detailed textual description of the desired video motion, camera path, lighting, and styling. Use `@Image{i}` to reference input images and `@Element{i}` to reference visual elements. |
| image_urls | Yes | array[string] | Array of URLs pointing to reference images. Use `@Image1`, `@Image2`, etc. in the prompt to reference these in order. Must contain at least one image. |
| elements | No | array[object] | Array of visual elements to anchor in the video. Each element contains reference images and a frontal image for identity consistency. |
| elements[i].reference_image_urls | No | array[string] | Array of URLs for additional reference images of the element (e.g., side views, poses) to improve consistency. |
| elements[i].frontal_image_url | No | string | Front-facing image of the element used to preserve identity throughout motion (e.g., character face or object front). |
| duration | No | string | Duration of the generated video in seconds. Valid values: "3", "5", "10". |
| aspect_ratio | No | string | Aspect ratio of the output video. Valid values: "16:9", "9:16", "1:1". |
**Example Request**
```
{
"prompt": "Take @Image1 as the start frame. Start with a high-angle satellite view of the ancient greenhouse ruin surrounded by nature. The camera swoops down and flies inside the building, revealing the character from @Element1 standing in the sun-drenched center. The camera then seamlessly transitions into a smooth 180-degree orbit around the character, moving to the back view. As the open backpack comes into focus, the camera continues to push forward, zooming deep inside the bag to reveal the glowing stone from @Element2 nestled inside. Cinematic lighting, hopeful atmosphere, 35mm lens. Make sure to keep it as the style of @Image2.",
"image_urls": [
"https://...23FGBYdGLgbK3u.png",
"https://...uKQFSE7A7c5uUeUF.png"
],
"elements": [
{
"reference_image_urls": [
"https://...t9xugpOTQyZW0O.png",
"https://...NyJ6bnpa_xBue-K.png"
],
"frontal_image_url": "...qshvMZROKh9lW3.png"
},
{
"reference_image_urls": [
"https://...Wihspyv4pp6hgj7D.png"
],
"frontal_image_url": "https://...HJlgcaTyR5Ujj2H.png"
}
],
"duration": "5",
"aspect_ratio": "16:9"
}
```
**Response**
```
{
"request_id": "kling-o1-reference-image-to-video-382_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/kling-o1-reference-image-to-video-382_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```
#### Kling O1 Reference Video To Video API Documentation
https://gateway.pixazo.ai/kling-o1-reference-video-to-video-315/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Generate Request - Kling O1 Reference Video to Video API
**Request Code**
```
POST https://gateway.pixazo.ai/kling-o1-reference-video-to-video-315/v1/kling-o1-reference-video-to-video-request
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"prompt": "Based on @Video1, generate the next shot. Keep the style of the video.",
"video_url": "https://example.com/media/scene1.mp4",
"elements": [
{
"reference_image_urls": [
"https://example.com/images/style_frame_1.png"
],
"frontal_image_url": "https://example.com/images/frontal_pose.png"
}
],
"aspect_ratio": "16:9",
"duration": "5"
}
```
**Output**
```
{
"request_id": "kling-o1-reference-video-to-video-315_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/kling-o1-reference-video-to-video-315_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Generate Request
| Parameter | Required | Type | Description |
| prompt | Yes | string | Text description guiding the generation of the next shot. Should reference the reference video and desired style or action. |
| video_url | Yes | string | Publicly accessible URL to the input reference video. Must be a stable link (e.g., HTTPS). |
| image_urls | No | array of strings | Array of image URLs to supplement visual context. These are ignored if `elements` is provided. |
| elements | No | array of objects | Array of element objects for advanced control over specific regions. Each element defines a reference image and frontal view. This overrides `image_urls`. |
| elements.reference_image_urls | Yes (within elements) | array of strings | Array of reference images to guide the visual style of the generated segment. Used in conjunction with `frontal_image_url`. |
| elements.frontal_image_url | Yes (within elements) | string | Frontal view image of a subject to maintain consistency in pose or facial appearance. |
| aspect_ratio | No | string | Desired aspect ratio of the output video. "auto" scales to match the input video. Values: "auto", "1:1", "16:9", "9:16". |
| duration | No | string | Desired duration of the generated video in seconds. Accepts integer values as strings (e.g., "5", "10"). |
**Example Request**
```
{
"prompt": "Based on @Video1, generate the next shot. Keep the style of the video.",
"video_url": "https://example.com/media/scene1.mp4",
"elements": [
{
"reference_image_urls": [
"https://example.com/images/style_frame_1.png"
],
"frontal_image_url": "https://example.com/images/frontal_pose.png"
}
],
"aspect_ratio": "16:9",
"duration": "5"
}
```
**Response**
```
{
"request_id": "kling-o1-reference-video-to-video-315_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/kling-o1-reference-video-to-video-315_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```
#### 8. Kling v1
#### Kling v1 Virtual Try-On API Documentation
https://gateway.pixazo.ai/kling-ai-vton/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Generate Image Request - Kling Virtual Try On API
**Request Code**
```
POST https://gateway.pixazo.ai/kling-ai-vton/v1/getVirtualTryOnTask
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"human_image": "https://storage.googleapis.com/imagesai.appypie.com/testing/00034_00.jpg",
"image_tail": "https://storage.googleapis.com/imagesai.appypie.com/testing/04469_00.jpg",
"callback_url": ""
}
```
**Output**
```
{
"request_id": "kling-virtual-try-on_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/kling-virtual-try-on_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Generate Image Request
| Parameter | Required | Type | Description |
| human_image | Yes | string | Support inputting image Base64 encoding or image URL (ensure accessibility). Ex: https://storage.googleapis.com/imagesai.appypie.com/testing/00034_00.jpg. Please note, if you use the Base64 method, make sure all image data parameters you pass are in Base64 encoding format. When submitting data, do not add any prefixes to the Base64-encoded string, such as data:image/png;base64. The correct parameter format should be the Base64-encoded string itself. Supported image formats include.jpg / .jpeg / .png . The image file size cannot exceed 10MB, and the image resolution should not be less than 300*300px |
| image_tail | No | string | Default: null, Support inputting image Base64 encoding or image URL (ensure accessibility). Ex: https://storage.googleapis.com/imagesai.appypie.com/testing/04469_00.jpg. Please note, if you use the Base64 method, make sure all image data parameters you pass are in Base64 encoding format. When submitting data, do not add any prefixes to the Base64-encoded string, such as data:image/png;base64. The correct parameter format should be the Base64-encoded string itself. Supported image formats include.jpg / .jpeg / .png . The image file size cannot exceed 10MB, and the image resolution should not be less than 300*300px |
| callback_url | No | string | Default: None. The callback notification address for the result of this task. If configured, the server will actively notify when the task status changes. The specific message schema of the notification can be found in "Callback Protocol" |
**Example Request**
```
{
"human_image": "https://storage.googleapis.com/imagesai.appypie.com/testing/00034_00.jpg",
"image_tail": "https://storage.googleapis.com/imagesai.appypie.com/testing/04469_00.jpg",
"callback_url": ""
}
```
**Response**
```
{
"request_id": "kling-virtual-try-on_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/kling-virtual-try-on_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	Your subscription key
```

---

### Pika API - AI Video Generation APIs
**Page:** https://www.pixazo.ai/models/pika


by Pika Labs

Pika API, developers can harness Pika's unique artistic capabilities for generating videos with distinctive visual styles. The API excels at creative video generation, making it popular among artists, content creators, and brands seeking unique video content.

#### API Documentation Coming Soon

Comprehensive API documentation for Pika is currently being prepared. Check back soon for detailed endpoint information, code examples, and integration guides.

Stay Updated

Get notified when documentation is available.

---

## Image Generation & Editing

### Flux 2 Pro API, Flux 2 Klein, Flux 1.1 Pro, 2 Dev, 1.0 (Free) API - AI Image Generation APIs
**Page:** https://www.pixazo.ai/models/flux


by Black Forest Labs

Flux 2 Pro API, developers can access all Flux versions for generating highly detailed, photorealistic images from text prompts. The API supports advanced features like image-to-image transformation, style control, and batch processing for production workflows.

Models Version
Flux Fill Dev
Flux 2 Pro
Flux 2 Klein
Flux 2 Dev
Flux Pro 1.1
Flux 1 Schnell - FREE
Flux Pro
Flux Dev
Image Inpainting
Image Inpainting
**Request Code**
**Request Parameters**
**Example Request**
**Response**
**Request Headers**
**Response Handling**
**Pricing**
#### Flux Fill Dev Image Inpainting API Documentation
https://gateway.pixazo.ai/flux-fill-dev/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Image Generation Request - Flux Fill Dev API
**Request Code**
```
POST https://gateway.pixazo.ai/flux-fill-dev/v1/flux-fill/generate
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"prompt": "a futuristic spaceship with neon lights",
"image": "https://example.com/photo.png",
"mask": "https://example.com/mask.png"
}
```
**Output**
```
{
"request_id": "flux-fill-dev_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/flux-fill-dev_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Image Generation Request
| Parameter | Required | Type | Description |
| prompt | Yes | string | Text description of what to generate in the masked area |
| image | Yes | string | URL of the source image to inpaint. Must be publicly accessible |
| mask | Yes | string | URL of the mask image. White areas = fill/regenerate, black areas = preserve |
| seed | No | integer | Random seed for reproducible results |
| guidance | No | number | Guidance scale for prompt adherence (default: 30) |
| num_outputs | No | integer | Number of output images to generate (1-4, default: 1) |
| num_inference_steps | No | integer | Number of denoising steps (default: 28). Higher = better quality but slower |
| megapixels | No | string | Output resolution. Values: "0.25", "1" (default), "match_input" |
| output_format | No | string | Output format: "webp" (default), "jpg", "png" |
| output_quality | No | integer | Output quality 0-100 (default: 80) |
| lora_scale | No | number | LoRA adapter strength (default: 1) |
| disable_safety_checker | No | boolean | Disable NSFW filter (default: false) |
**Example Request**
```
{
"prompt": "a futuristic spaceship with neon lights",
"image": "https://example.com/photo.png",
"mask": "https://example.com/mask.png",
"seed": 42,
"guidance": 35,
"num_outputs": 2,
"num_inference_steps": 35,
"output_format": "png",
"output_quality": 95
}
```
**Response**
```
{
"request_id": "flux-fill-dev_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/flux-fill-dev_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```
#### 2. Flux 2 Pro
#### Flux 2 Pro Image To Image API Documentation
https://gateway.pixazo.ai/flux-2-pro-image-to-image-866/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Generate Request - flux 2 pro Image to Image API
**Request Code**
```
POST https://gateway.pixazo.ai/flux-2-pro-image-to-image-866/v1/flux-2-pro-image-to-image-request
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"prompt": "Place realistic flames emerging from the top of the coffee cup, dancing above the rim",
"image_size": "auto",
"safety_tolerance": "2",
"enable_safety_checker": true,
"output_format": "jpeg",
"image_urls": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/nano-banana/nano-banana-a382a80b-f8df-4de1-a0c1-a5dcfd42dae4-1758783383399.jpg"
]
}
```
**Output**
```
{
"request_id": "flux-2-pro-image-to-image-866_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/flux-2-pro-image-to-image-866_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Generate Request
| Parameter | Required | Type | Description |
| prompt | Yes | string | A descriptive text prompt guiding the image transformation (e.g., style, lighting, elements to add or remove) |
| image_urls | Yes | array[string] | One or more publicly accessible image URLs to be edited. Only the first image will be processed if multiple are provided. |
| image_size | No | string | Dimensions of the output image. Use "auto" to preserve input dimensions, or specify "512x512", "1024x1024", etc. |
| safety_tolerance | No | string | Controls sensitivity of content moderation. Higher values allow more permissive outputs. Accepts "1" (strict), "2" (moderate), "3" (relaxed). |
| enable_safety_checker | No | boolean | Enables or disables content safety filtering. When false, safety checks are bypassed (use with caution). |
| output_format | No | string | Format of the generated image output. Supported values: "jpeg", "png", "webp" |
**Example Request**
```
{
"prompt": "Place realistic flames emerging from the top of the coffee cup, dancing above the rim",
"image_size": "auto",
"safety_tolerance": "2",
"enable_safety_checker": true,
"output_format": "jpeg",
"image_urls": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/nano-banana/nano-banana-a382a80b-f8df-4de1-a0c1-a5dcfd42dae4-1758783383399.jpg"
]
}
```
**Response**
```
{
"request_id": "flux-2-pro-image-to-image-866_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/flux-2-pro-image-to-image-866_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```
#### Flux 2 Pro Image To Image Trainer API Documentation
https://gateway.pixazo.ai/flux-2-pro-image-to-image-trainer-831/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Generate Request - flux 2 pro Image to Image Trainer API
**Request Code**
```
POST https://gateway.pixazo.ai/flux-2-pro-image-to-image-trainer-831/v1/flux-2-pro-image-to-image-trainer-request
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"image_data_url": "https://example.com/images/reference_style.jpg",
"steps": 1200,
"learning_rate": 0.00007
}
```
**Output**
```
{
"request_id": "flux-2-pro-image-to-image-trainer-831_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/flux-2-pro-image-to-image-trainer-831_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Generate Request
| Parameter | Required | Type | Description |
| image_data_url | Yes | string | Base64-encoded URL or public HTTP/S URL pointing to a reference image used for training. Must be a high-resolution, high-quality image representative of the desired style or domain. |
| steps | No | integer | Number of training steps to perform. Higher values yield more refined models but require longer processing time. |
| learning_rate | No | number | Learning rate for the LoRA fine-tuning process. A lower value provides more stable training; higher values may converge faster but risk overfitting. |
**Example Request**
```
{
"image_data_url": "https://example.com/images/reference_style.jpg",
"steps": 1200,
"learning_rate": 0.00007
}
```
**Response**
```
{
"request_id": "flux-2-pro-image-to-image-trainer-831_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/flux-2-pro-image-to-image-trainer-831_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```
#### Flux 2 Pro Text To Image API Documentation
https://gateway.pixazo.ai/flux-2-pro-text-to-image-799/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Generate Request - flux 2 pro Text to Image API
**Request Code**
```
POST https://gateway.pixazo.ai/flux-2-pro-text-to-image-799/v1/flux-2-pro-text-to-image-request
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"prompt": "An intense close-up of knight's visor reflecting battle, sword raised, flames in background, chiaroscuro helmet shadows, hyper-detailed armor, square medieval, cinematic lighting",
"image_size": "landscape_4_3",
"safety_tolerance": 2,
"enable_safety_checker": true,
"output_format": "jpeg"
}
```
**Output**
```
{
"request_id": "flux-2-pro-text-to-image-799_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/flux-2-pro-text-to-image-799_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Generate Request
| Parameter | Required | Type | Description |
| prompt | Yes | string | A detailed text description of the desired image. Be specific about subject, style, lighting, composition, and mood. |
| image_size | No | string | The aspect ratio and dimensions of the generated image. Supported values: portrait_4_5, portrait_9_16, square_1_1, landscape_3_2, landscape_4_3, landscape_16_9, landscape_21_9. |
| safety_tolerance | No | integer | Controls sensitivity of the safety filter. Lower values (1-2) are more restrictive; higher values (3-4) allow more expressive content. |
| enable_safety_checker | No | boolean | Whether to enable content safety filtering. Disabling may expose you to inappropriate content and is not recommended for public applications. |
| output_format | No | string | The file format of the generated image. Supported values: jpeg, png, webp. |
**Example Request**
```
{
"prompt": "An intense close-up of knight's visor reflecting battle, sword raised, flames in background, chiaroscuro helmet shadows, hyper-detailed armor, square medieval, cinematic lighting",
"image_size": "landscape_4_3",
"safety_tolerance": 2,
"enable_safety_checker": true,
"output_format": "jpeg"
}
```
**Response**
```
{
"request_id": "flux-2-pro-text-to-image-799_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/flux-2-pro-text-to-image-799_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```
#### Flux 2 Pro Text To Image Trainer API Documentation
https://gateway.pixazo.ai/flux-2-pro-text-to-image-trainer-712/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Generate Request - flux 2 pro Text to Image Trainer API
**Request Code**
```
POST https://gateway.pixazo.ai/flux-2-pro-text-to-image-trainer-712/v1/flux-2-pro-text-to-image-trainer-request
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"image_data_url": "https://example.com/dataset.zip",
"steps": 1500,
"learning_rate": 0.00003
}
```
**Output**
```
{
"request_id": "flux-2-pro-text-to-image-trainer-712_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/flux-2-pro-text-to-image-trainer-712_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Generate Request
| Parameter | Required | Type | Description |
| image_data_url | Yes | string | Base64-encoded URL or HTTP(S) endpoint pointing to a dataset of training images (PNG/JPG). Must include multiple samples (minimum 10 recommended) to train a robust LoRA model. |
| steps | No | integer | Number of training steps to perform. Higher values improve model quality but increase training time. |
| learning_rate | No | number | Learning rate for the LoRA adapter training process. Lower values ensure stable convergence; higher values may speed up training but risk instability. |
**Example Request**
```
{
"image_data_url": "https://example.com/dataset.zip",
"steps": 1500,
"learning_rate": 0.00003
}
```
**Response**
```
{
"request_id": "flux-2-pro-text-to-image-trainer-712_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/flux-2-pro-text-to-image-trainer-712_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```
#### 3. Flux 2 Klein
#### Flux 2 Klein Text To Image API Documentation
**Request Code**
```
POST https://gateway.pixazo.ai/flux-2-klein-4b/v1/generateImage
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"prompt": "A sunset with a dog playing on the beach, golden light reflecting on the water, photorealistic, highly detailed",
"steps": 25,
"width": 1024,
"height": 1024
}
```
**Output**
```
{
"output": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/flux-2-klein-4b/1768578707564-851083.png"
}
```
Request Parameters - Text to Image
| Parameter | Required | Type | Description |
| prompt | Yes | string | The text query that instructs the AI model on what kind of content to generate. |
| steps | No | integer | The number of diffusion steps; higher values can improve quality but take longer. Default: 25 |
| width | No | integer | The desired width of the generated image, specified in pixels. Default: 1024. Supported sizes: 512, 1024, 1448, 2048 |
| height | No | integer | The desired height of the generated image, specified in pixels. Default: 1024. Supported sizes: 512, 1024, 1448, 2048 |
**Example Request**
```
{
"prompt": "A sunset with a dog playing on the beach, golden light reflecting on the water, photorealistic, highly detailed",
"steps": 25,
"width": 1024,
"height": 1024
}
```
**Response**
```
{
"output": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/flux-2-klein-4b/1768578707564-851083.png"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```
#### 4. Flux 2 Dev
#### Flux 2 Dev Text To Image API Documentation
**Request Code**
```
POST https://gateway.pixazo.ai/generateT2I
Content-Type: application/json
X-Secret-Key: YOUR_SECRET_KEY
{
"prompt": "a sunset at the alps",
"steps": 25,
"width": 1024,
"height": 1024
}
```
**Output**
```
{
"output": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/flux-2-dev-cf/1762439647765-844809.png"
}
```
Request Parameters - generateT2I
| Parameter | Required | Type | Description |
| prompt | Yes | string | The text prompt used to generate the image. Describes the style and content for the generated image. The prompt is automatically sanitized to avoid content moderation false positives (e.g., "fingers brushing lips" is converted to "hand near face"). |
| steps | No | integer | Number of inference steps for image generation. Controls the quality and detail level of the generated image. Higher values may produce more refined results but take longer to generate. |
| width | No | integer | Width of the generated image in pixels. Must be a positive integer. Recommended values: 512, 768, 1024, 1280, 1536, 2048. |
| height | No | integer | Height of the generated image in pixels. Must be a positive integer. Recommended values: 512, 768, 1024, 1280, 1536, 2048. |
**Example Request**
```
{
"prompt": "a sunset at the alps",
"steps": 25,
"width": 1024,
"height": 1024
}
```
**Response**
```
{
"output": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/flux-2-dev-cf/1762439647765-844809.png"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
X-Secret-Key	YOUR_SECRET_KEY
```
#### 5. Flux Pro 1.1
#### Flux Pro 1.1 Text To Image API Documentation
https://gateway.pixazo.ai/pro1.1/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Pro1.1 Ultra generateRequest - Flux pro 1.1
**Request Code**
```
POST https://gateway.pixazo.ai/pro1.1/v1/pro1.1ultra/generateRequest
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"prompt": "A futuristic cityscape at sunset",
"seed": 43,
"output_format": "jpeg",
"aspect_ratio": "16:9"
}
```
**Output**
```
{
"request_id": "flux-pro-1-1_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/flux-pro-1-1_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Pro1.1 Ultra generateRequest
| Parameter | Required | Type | Description |
| prompt | Yes | string | The instruction or description for the image to be generated. FLUX1.1 [pro] ultra delivers professional-grade image quality with enhanced photo realism and up to 2K resolution |
| seed | Optional | integer | The same seed and prompt will output the same image every time |
| sync_mode | Optional | boolean | If true, waits for image generation and upload before returning response. Increases latency but provides direct image access without CDN |
| num_images | Optional | integer | The number of images to generate |
| enable_safety_checker | Optional | boolean | Whether to enable the safety checker to filter NSFW content |
| output_format | Optional | string | The format of the generated image. Values: "jpeg", "png" |
| safety_tolerance | Optional | string | The safety tolerance level for generated images. 1 is most strict, 6 is most permissive. Values: "1", "2", "3", "4", "5", "6" |
| enhance_prompt | Optional | boolean | Whether to enhance the prompt for better results |
| aspect_ratio | Optional | string | The aspect ratio of the generated image. Values: "21:9", "16:9", "4:3", "3:2", "1:1", "2:3", "3:4", "9:16", "9:21" |
| raw | Optional | boolean | Generate less processed, more natural-looking images |
**Example Request**
```
{
"prompt": "A futuristic cityscape at sunset",
"seed": 43,
"output_format": "jpeg",
"aspect_ratio": "16:9"
}
```
**Response**
```
{
"request_id": "flux-pro-1-1_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/flux-pro-1-1_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```
#### 6. Flux 1 Schnell
#### Flux 1 Schnell Text To Image API Documentation
**Request Code**
```
POST https://gateway.pixazo.ai/flux-1-schnell/v1/getData
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"prompt": "Picture a sleek, futuristic car racing through a neon-lit cityscape, its engine humming efficiently as it blurs past digital billboards. The driver skillfully navigates the glowing streets, aiming for victory in this high-tech, adrenaline-fueled race of tomorrow.",
"num_steps": 4,
"seed": 15,
"height": 512,
"width": 512
}
```
**Output**
```
{
"output": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/flux-schnell-cf/prompt-1768311018384-879091.png"
}
```
Request Parameters - Get Image
| Parameter | Required | Type | Description |
| prompt | Yes | string | The text query that instructs the AI model on what kind of content to generate. |
| num_steps | No | integer | The number of diffusion steps; higher values can improve quality but take longer. Default: 4, Maximum: 8 |
| seed | No | integer | A "seed" is used to generate a consistent sequence of pseudo-random numbers, aiding reproducibility. |
| height | No | integer | The desired height of the generated image, specified in pixels. Default: 1024 |
| width | No | integer | The desired width of the generated image, specified in pixels. Default: 1024 |
**Example Request**
```
{
"prompt": "Picture a sleek, futuristic car racing through a neon-lit cityscape, its engine humming efficiently as it blurs past digital billboards. The driver skillfully navigates the glowing streets, aiming for victory in this high-tech, adrenaline-fueled race of tomorrow.",
"num_steps": 4,
"seed": 15,
"height": 512,
"width": 512
}
```
**Response**
```
{
"output": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/flux-schnell-cf/prompt-1768311018384-879091.png"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```
#### Flux 1 Schnell Text To Image(Batch) API Documentation
**Request Code**
```
POST https://gateway.pixazo.ai/flux-1-schnell/v1/getDataBatch
Content-Type: application/json
X-Secret-Key: YOUR_SECRET_KEY
Cache-Control: no-cache
{
"prompt": "Picture a handsome man dancing",
"num_steps": 4,
"seed": 15,
"height": 512,
"width": 512,
"webhook_url": "https://your-domain.com/webhook"
}
```
**Output**
```
{
"requestId": "18a36237-f8b2-4c8d-9a3b-d5e8a9f12c45",
"status": "queued",
"message": "Request queued. Result will be sent to the provided webhook URL.",
"pollingEndpoint": "/checkStatus",
"pollingInstructions": "POST to /checkStatus with {...}"
}
```
Request Parameters - Get Image Batch
| Parameter | Required | Type | Description |
| prompt | Yes | string | The text prompt used to generate the image. Describes the style and content for the generated image. The prompt is automatically sanitized to avoid content moderation false positives. |
| num_steps | No | integer | Number of inference steps for image generation. FLUX-1-Schnell is optimized for speed, so values between 1-4 are typical. |
| seed | No | integer | Random seed for reproducible image generation. Use the same seed to generate similar images. If not provided, a random seed is generated. |
| width | No | integer | Width of the generated image in pixels. Recommended values: 512, 768, 1024, 1280, 1536, 1920. |
| height | No | integer | Height of the generated image in pixels. Recommended values: 512, 768, 1024, 1280, 1536, 1920. |
| webhook_url | No | string | URL to receive the result via HTTP POST when processing completes. If provided, the API returns immediately with status 202. If omitted, the API attempts internal polling for up to 60 seconds. |
**Example Request**
```
{
"prompt": "Picture a handsome man dancing",
"num_steps": 4,
"seed": 15,
"height": 512,
"width": 512,
"webhook_url": "https://your-domain.com/webhook"
}
```
**Response**
```
{
"requestId": "18a36237-f8b2-4c8d-9a3b-d5e8a9f12c45",
"status": "queued",
"message": "Request queued. Result will be sent to the provided webhook URL.",
"pollingEndpoint": "/checkStatus",
"pollingInstructions": "POST to /checkStatus with {\"requestId\": \"18a36237-f8b2-4c8d-9a3b-d5e8a9f12c45\"}"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
X-Secret-Key	YOUR_SECRET_KEY
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```
#### 7. Flux Pro
#### Flux Pro Text To Image API Documentation
https://gateway.pixazo.ai/flux-pro/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Text To Image - Flux Pro API
**Request Code**
```
POST https://gateway.pixazo.ai/flux-pro/v1/pro/textToImage
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"prompt": "A futuristic cityscape at sunset with flying cars",
"image_size": "landscape_4_3"
}
```
**Output**
```
{
"request_id": "flux-pro_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/flux-pro_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Text To Image
| Parameter | Required | Type | Description |
| prompt | Yes | string | The instruction or description for the image to be generated |
| image_size | Optional | string | The aspect ratio of the generated image, Possible enum values: square_hd, square, portrait_4_3, portrait_16_9, landscape_4_3, landscape_16_9 |
| num_inference_steps | Optional | integer | The number of denoising steps. Higher values result in higher quality images but take longer to generate |
| guidance_scale | Optional | float | Controls how closely the model follows the prompt. Higher values make the model adhere more closely to the prompt |
| num_images | Optional | integer | The number of images to generate |
| enable_safety_checker | Optional | boolean | Whether to enable the safety checker to filter NSFW content |
| output_format | Optional | string | The format of the generated image. Values: "jpeg", "png" |
**Example Request**
```
{
"prompt": "A futuristic cityscape at sunset with flying cars",
"image_size": "landscape_4_3"
}
```
**Response**
```
{
"request_id": "flux-pro_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/flux-pro_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```
#### 8. Flux Dev
#### Flux Dev Image To Image API Documentation
https://gateway.pixazo.ai/flux-dev/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Image To Image - flux Dev API
**Request Code**
```
POST https://gateway.pixazo.ai/flux-dev/v1/dev/imageToImage
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"image_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/model.png",
"prompt": "Editorial rooftop shot, woman in peach tee and denim, modern urban backdrop, bold blue tones, polished aesthetic."
}
```
**Output**
```
{
"request_id": "flux-dev_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/flux-dev_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Image To Image
| Parameter | Required | Type | Description |
| image_url | Yes | string | The URL of the source image to transform |
| prompt | Yes | string | The instruction or description for how to transform the image |
| strength | Optional | float | The strength of the transformation. Higher values result in more dramatic changes |
| num_inference_steps | Optional | integer | The number of denoising steps. Higher values result in higher quality images but take longer to generate |
| guidance_scale | Optional | float | Controls how closely the model follows the prompt. Higher values make the model adhere more closely to the prompt |
| num_images | Optional | integer | The number of images to generate |
| enable_safety_checker | Optional | boolean | Whether to enable the safety checker to filter NSFW content |
| output_format | Optional | string | The format of the generated image. Values: "jpeg", "png" |
| acceleration | Optional | string | The speed of generation. Values: "none", "regular", "high" |
**Example Request**
```
{
"image_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/model.png",
"prompt": "Editorial rooftop shot, woman in peach tee and denim, modern urban backdrop, bold blue tones, polished aesthetic."
}
```
**Response**
```
{
"request_id": "flux-dev_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/flux-dev_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```
#### Flux Dev Text To Image API Documentation
https://gateway.pixazo.ai/flux-dev/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Text To Image - flux Dev API
**Request Code**
```
POST https://gateway.pixazo.ai/flux-dev/v1/dev/textToImage
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"prompt": "A futuristic city skyline at sunset with flying cars",
"image_size": "landscape_4_3"
}
```
**Output**
```
{
"request_id": "flux-dev_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/flux-dev_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Text To Image
| Parameter | Required | Type | Description |
| prompt | Yes | string | The instruction or description for the image to be generated |
| image_size | No | string | The aspect ratio of the generated image, Possible enum values: square_hd, square, portrait_4_3, portrait_16_9, landscape_4_3, landscape_16_9 |
| num_inference_steps | No | integer | The number of denoising steps. Higher values result in higher quality images but take longer to generate |
| guidance_scale | No | float | Controls how closely the model follows the prompt. Higher values make the model adhere more closely to the prompt |
| num_images | No | integer | The number of images to generate |
| enable_safety_checker | No | boolean | Whether to enable the safety checker to filter NSFW content |
| output_format | No | string | The format of the generated image. Values: "jpeg", "png" |
| acceleration | No | string | The speed of generation. Values: "none", "regular", "high" |
**Example Request**
```
{
"prompt": "A futuristic city skyline at sunset with flying cars",
"image_size": "landscape_4_3"
}
```
**Response**
```
{
"request_id": "flux-dev_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/flux-dev_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```

---

### GPT Image 1.5 API - AI Image Generation APIs
**Page:** https://www.pixazo.ai/models/gpt-image


by OpenAI

GPT Image 1.5 API, developers can create images that accurately reflect complex prompts, edit existing images with natural language instructions, and generate variations. The API combines GPT's language understanding with powerful image generation for intuitive creative workflows.

Models Version
GPT Image v1.5
Text To Image
Text To Image
**Request Code**
**Request Parameters**
**Example Request**
**Response**
**Request Headers**
**Response Handling**
**Pricing**
#### GPT Image v1.5 Text To Image API Documentation
https://gateway.pixazo.ai/gpt-image-1-5-api-923/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
GPT-Image 1.5 API generate request - GPT Image 1.5 API
**Request Code**
```
POST https://gateway.pixazo.ai/gpt-image-1-5-api-923/v1/gpt-image-1-5-api-request
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"prompt": "create a realistic image taken with iphone at these coordinates 41°43′32″N 49°56′49″W 15 April 1912",
"image_size": "1024x1024",
"background": "auto",
"quality": "high",
"num_images": 1,
"output_format": "png"
}
```
**Output**
```
{
"request_id": "gpt-image-1-5-api-923_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/gpt-image-1-5-api-923_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - GPT-Image 1.5 API generate request
| Parameter | Required | Type | Description |
| prompt | Yes | string | A detailed text description of the desired image. The model interprets this prompt to generate visuals with high fidelity. |
| image_size | Optional | string | Dimensions of the generated image. Supports standard aspect ratios. |
| background | Optional | string | Background handling mode. Use "auto" for intelligent background inference, or specify a color/texture if supported. |
| quality | Optional | string | Rendering quality level. "high" produces more detailed outputs with longer processing. "standard" is faster but less detailed. |
| num_images | Optional | integer | Number of distinct images to generate in a single request. |
| output_format | Optional | string | File format of the generated image. Supported formats: "png", "jpeg", "webp". |
**Example Request**
```
{
"prompt": "create a realistic image taken with iphone at these coordinates 41°43′32″N 49°56′49″W 15 April 1912",
"image_size": "1024x1024",
"background": "auto",
"quality": "high",
"num_images": 1,
"output_format": "png"
}
```
**Response**
```
{
"request_id": "gpt-image-1-5-api-923_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/gpt-image-1-5-api-923_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```

---

### Ideogram 2.0 API, Ideogram Turbo API - AI Image Generation APIs
**Page:** https://www.pixazo.ai/models/ideogram

> Ideogram 2.0 creates images with accurate text rendering — logos, signs, typography. Offers standard (V_2) and turbo (V_2_TURBO) variants, plus editing, remixing, and image description.

#### 1. Ideogram v2 — Text To Image

**Endpoint:**
```
POST https://gateway.pixazo.ai/ideogramV_2/v1/generate
```

**Headers:**
```
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
```

**Request Body:**
```json
{
    "image_request": {
        "prompt": "A serene tropical beach scene with tall palm trees...",
        "negative_prompt": "blur",
        "model": "V_2",
        "aspect_ratio": "ASPECT_10_16",
        "magic_prompt_option": "AUTO",
        "seed": 212,
        "style_type": "AUTO",
        "color_palette": {
            "name": "JUNGLE"
        }
    }
}
```

**Parameters:**

| Parameter | Required | Type | Description |
|-----------|----------|------|-------------|
| prompt | Yes | string | Text describing the scene or image to generate |
| negative_prompt | No | string | Elements or features to avoid |
| model | Yes | string | `V_2` |
| aspect_ratio | No | enum | Default: `ASPECT_1_1`. Options: `ASPECT_10_16`, `ASPECT_16_10`, `ASPECT_9_16`, `ASPECT_16_9`, `ASPECT_3_2`, `ASPECT_2_3`, `ASPECT_4_3`, `ASPECT_3_4`, `ASPECT_1_3`, `ASPECT_3_1` |
| seed | No | integer | Random seed (1–9999999999) |
| magic_prompt_option | No | enum | Default: `AUTO`. Options: `ON`, `OFF` |
| style_type | No | enum | Default: `AUTO`. Options: `GENERAL`, `REALISTIC`, `DESIGN`, `RENDER_3D`, `ANIME` |
| color_palette | No | object | Preset name: `EMBER`, `FRESH`, `JUNGLE`, `MAGIC`, `MELON`, `MOSAIC`, `PASTEL`, `ULTRAMARINE` |

**Response:**
```json
{
    "created": "2024-11-01T10:06:14.744267+00:00",
    "data": [
        {
            "is_image_safe": true,
            "prompt": "A serene tropical beach scene...",
            "resolution": "800x1280",
            "seed": 212,
            "style_type": "REALISTIC",
            "url": "https://ideogram.ai/api/images/ephemeral/...png"
        }
    ]
}
```

**Pricing:** $0.20 per generation (all resolutions)

---

#### 2. Ideogram v2 — Describe Image

**Endpoint:**
```
POST https://gateway.pixazo.ai/ideogramV_2/v1/describe
```

**Headers:**
```
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
Content-Type: multipart/form-data
```

**Parameters:**

| Parameter | Required | Type | Description |
|-----------|----------|------|-------------|
| image_file | Yes | File | The image file to describe |

**Example:**
```
--form 'image_file=@/path/to/your/image.png'
```

**Response:**
```json
{
    "created": "2024-11-01T10:06:14.744267+00:00",
    "data": {
        "description": "The image depicts a serene tropical beach scene with tall palm trees and azure waters."
    }
}
```

**Pricing:** $0.20 per request

---

#### 3. Ideogram v2 — Edit Image

**Endpoint:**
```
POST https://gateway.pixazo.ai/ideogramV_2/v1/edit
```

**Headers:**
```
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
Content-Type: multipart/form-data
```

**Parameters:**

| Parameter | Required | Type | Description |
|-----------|----------|------|-------------|
| image_file | Yes | File | The image file to edit |
| mask | No | File | Mask determining which parts of the image to affect |
| prompt | Yes | string | Text description of transformations to apply |
| model | Yes | string | `V_2` |

**Example:**
```http
POST https://gateway.pixazo.ai/ideogramV_2/v1/edit
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
Content-Type: multipart/form-data

--boundary
Content-Disposition: form-data; name="image_file"; filename="image.png"
Content-Type: image/png
[File content]
--boundary
Content-Disposition: form-data; name="mask"; filename="mask.png"
Content-Type: image/png
[File content]
--boundary
Content-Disposition: form-data; name="prompt"
Enhance brightness and remove text
--boundary
Content-Disposition: form-data; name="model"
V_2
--boundary--
```

**Response:**
```json
{
    "created": "2024-11-01T10:06:14.744267+00:00",
    "data": [
        {
            "is_image_safe": true,
            "prompt": "Enhance brightness and remove text",
            "edited_image": "https://ideogram.ai/api/images/ephemeral/...png"
        }
    ]
}
```

**Pricing:** $0.20 per edit

---

#### 4. Ideogram v2 — Remix Image

**Endpoint:**
```
POST https://gateway.pixazo.ai/ideogramV_2/v1/remix
```

**Headers:**
```
Ocp-Apim-Subscription-Key: YOUR_Subscription_KEY
Content-Type: multipart/form-data
```

**Parameters:**

| Parameter | Required | Type | Description |
|-----------|----------|------|-------------|
| image_request | Yes | JSON | JSON string with prompt, aspect_ratio, image_weight, magic_prompt_option, model |
| image_file | Yes | File | The image file to remix |

**Example (image_request JSON):**
```json
{
    "prompt": "A serene tropical beach scene...",
    "aspect_ratio": "ASPECT_10_16",
    "image_weight": 50,
    "magic_prompt_option": "ON",
    "model": "V_2"
}
```

**Response:**
```json
{
    "created": "2024-11-01T10:06:14.744267+00:00",
    "data": [
        {
            "is_image_safe": true,
            "prompt": "A serene tropical beach scene...",
            "resolution": "800x1280",
            "image_url": "https://ideogram.ai/api/images/ephemeral/...png"
        }
    ]
}
```

**Pricing:** $0.20 per remix

---

#### 5. Ideogram Turbo — Text To Image

**Endpoint:**
```
POST https://gateway.pixazo.ai/ideogramV_2_Turbo/v1/generate
```

**Headers:**
```
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
```

**Request Body:**
```json
{
    "image_request": {
        "prompt": "A serene tropical beach scene...",
        "negative_prompt": "blur",
        "model": "V_2_TURBO",
        "aspect_ratio": "ASPECT_10_16",
        "magic_prompt_option": "AUTO",
        "seed": 212,
        "style_type": "AUTO",
        "color_palette": {
            "name": "JUNGLE"
        }
    }
}
```

**Parameters:** Same as Ideogram v2 Text To Image, except `model` must be `V_2_TURBO`.

**Response:**
```json
{
    "created": "2024-11-01T10:06:14.744267+00:00",
    "data": [
        {
            "is_image_safe": true,
            "prompt": "A serene tropical beach scene...",
            "resolution": "800x1280",
            "seed": 212,
            "style_type": "REALISTIC",
            "url": "https://ideogram.ai/api/images/ephemeral/...png"
        }
    ]
}
```

**Pricing:** $0.20 per generation (all resolutions)

---

#### 6. Ideogram Turbo — Describe Image

**Endpoint:**
```
POST https://gateway.pixazo.ai/ideogramV_2_Turbo/v1/describe
```

**Headers:**
```
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
Content-Type: multipart/form-data
```

**Parameters:**

| Parameter | Required | Type | Description |
|-----------|----------|------|-------------|
| image_file | Yes | File | The image file to describe |

**Response:**
```json
{
    "created": "2024-11-01T10:06:14.744267+00:00",
    "data": {
        "description": "The image depicts a serene tropical beach scene..."
    }
}
```

**Pricing:** $0.20 per request

---

#### 7. Ideogram Turbo — Edit Image

**Endpoint:**
```
POST https://gateway.pixazo.ai/ideogramV_2_Turbo/v1/edit
```

**Headers:**
```
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
Content-Type: multipart/form-data
```

**Parameters:**

| Parameter | Required | Type | Description |
|-----------|----------|------|-------------|
| image_file | Yes | File | The image file to edit |
| mask | No | File | Mask determining which parts to affect |
| prompt | Yes | string | Text description of transformations |
| model | Yes | string | `V_2` |

**Response:**
```json
{
    "created": "2024-11-01T11:46:02.294543+00:00",
    "data": [
        {
            "is_image_safe": true,
            "prompt": "replace some text",
            "resolution": "1216x704",
            "seed": 875575135,
            "style_type": "GENERAL",
            "url": "https://ideogram.ai/api/images/ephemeral/...png"
        }
    ]
}
```

**Pricing:** $0.20 per edit

---

#### 8. Ideogram Turbo — Remix Image

**Endpoint:**
```
POST https://gateway.pixazo.ai/ideogramV_2_Turbo/v1/remix
```

**Headers:**
```
Ocp-Apim-Subscription-Key: YOUR_Subscription_KEY
Content-Type: multipart/form-data
```

**Parameters:**

| Parameter | Required | Type | Description |
|-----------|----------|------|-------------|
| image_request | Yes | JSON | JSON string with prompt, aspect_ratio, image_weight, magic_prompt_option, model (`V_2_TURBO`) |
| image_file | Yes | File | The image file to remix |

**Example (image_request JSON):**
```json
{
    "prompt": "A serene tropical beach scene...",
    "aspect_ratio": "ASPECT_10_16",
    "image_weight": 50,
    "magic_prompt_option": "ON",
    "model": "V_2_TURBO"
}
```

**Response:**
```json
{
    "created": "2000-01-23T04:56:07Z",
    "data": [
        {
            "prompt": "A serene tropical beach scene...",
            "resolution": "1024x1024",
            "is_image_safe": true,
            "seed": 12345,
            "url": "https://ideogram.ai/api/images/direct/...png",
            "style_type": "REALISTIC"
        }
    ]
}
```

**Pricing:** $0.20 per remix

**Status Codes (all Ideogram endpoints):**

| Code | Meaning |
|------|---------|
| 200 | Success |
| 400 | Bad Request |
| 401 | Unauthorized |
| 403 | Forbidden |
| 404 | Not Found |
| 429 | Too Many Requests |
| 500 | Internal Server Error |

---

### Seedream 5.0 API, Seedream 4.5 API, Seedream 4.0 API - AI Image Generation APIs
**Page:** https://www.pixazo.ai/models/seedream


by BytePlus

Seedream 5.0 API, developers can access text-to-image generation and advanced image editing features including multi-image editing. The API leverages ByteDance's extensive AI research to deliver high-quality visuals suitable for content creation and commercial applications.

Models Version
Seedream 5
Seedream 4.5
Seedream 4
Image To Image
Text To Image
Image To Image
**Request Code**
**Request Parameters**
**Example Request**
**Response**
**Request Headers**
**Response Handling**
**Pricing**
Text To Image
#### Seedream 5 Image To Image API Documentation
https://gateway.pixazo.ai/seedream-5-0-lite-image/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Seedream 5.0 Lite Image generate request - Seedream 5.0 Lite Image
**Request Code**
```
POST https://gateway.pixazo.ai/seedream-5-0-lite-image/v1/seedream-5-0-lite-image-request
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"prompt": "Transform the snowy winter tree in Figure 1 to match the blooming floral style of the tree in Figure 2. Keep the tree structure and composition but replace the snow with vibrant flowers and lush green foliage.",
"image_size": "auto_2K",
"num_images": 1,
"max_images": 1,
"enable_safety_checker": true,
"enhance_prompt_mode": "standard",
"image_urls": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/f1.png",
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/f2.png"
]
}
```
**Output**
```
{
"request_id": "seedream-5-0-lite-edit_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/seedream-5-0-lite-edit_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Seedream 5.0 Lite Image generate request
| Parameter | Required | Type | Description |
| prompt | Yes | string | Detailed text instruction describing the desired transformation, including references to input images (e.g., "Figure 1", "Figure 2"). |
| image_size | No | string | Target image resolution. Supported values: `square_hd`, `square`, `portrait_4_3`, `portrait_16_9`, `landscape_4_3`, `landscape_16_9`, `auto_2K`, `auto_3K`. |
| num_images | No | integer | Number of output images to generate. Must be between 1 and 6. |
| max_images | No | integer | Maximum number of images to return. Must be equal to or greater than `num_images`. |
| enable_safety_checker | No | boolean | Enables content safety filtering to block inappropriate outputs. |
| enhance_prompt_mode | No | string | Prompt enhancement strategy. Accepts: "none", "standard", "aggressive". |
| image_urls | Yes | array of strings | Array of HTTPS URLs pointing to reference images (up to 10). Images must be publicly accessible. |
**Example Request**
```
{
"prompt": "Transform the snowy winter tree in Figure 1 to match the blooming floral style of the tree in Figure 2. Keep the tree structure and composition but replace the snow with vibrant flowers and lush green foliage.",
"image_size": "auto_2K",
"num_images": 1,
"max_images": 1,
"enable_safety_checker": true,
"enhance_prompt_mode": "standard",
"image_urls": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/f1.png",
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/f2.png"
]
}
```
**Response**
```
{
"request_id": "seedream-5-0-lite-edit_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/seedream-5-0-lite-edit_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```
Seedream 5.0 Lite Image check status - Seedream 5.0 Lite Image
**Request Code**
```
POST https://gateway.pixazo.ai/seedream-5-0-lite-image/v1/seedream-5-0-lite-image-request-result
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"request_id": "abc123-def456-7890"
}
```
**Output**
```
{
"images": [
{
"url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/seedream-5-0-lite-edit/xvbT7A5LsRoFTWSmUXCtX_e9fa2bcda22249488eb78daa1fbe401d.png",
"content_type": "image/png",
"file_name": "e9fa2bcda22249488eb78daa1fbe401d.png",
"file_size": 6361422,
"width": null,
"height": null
}
]
}
```
Request Parameters - Seedream 5.0 Lite Image check status
| Parameter | Required | Type | Description |
| request_id | Yes | string | The request_id returned from the initial submission endpoint. |
**Example Request**
```
{
"request_id": "abc123-def456-7890"
}
```
**Response**
```
{
"images": [
{
"url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/seedream-5-0-lite-edit/xvbT7A5LsRoFTWSmUXCtX_e9fa2bcda22249488eb78daa1fbe401d.png",
"content_type": "image/png",
"file_name": "e9fa2bcda22249488eb78daa1fbe401d.png",
"file_size": 6361422,
"width": null,
"height": null
}
]
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```
#### Seedream 5 Text To Image API Documentation
https://gateway.pixazo.ai/seedream-5-0-lite-text-to-image/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Seedream 5.0 Lite Text to Image generate request - Seedream 5.0 Lite Text to Image
**Request Code**
```
POST https://gateway.pixazo.ai/seedream-5-0-lite-text-to-image/v1/seedream-5-0-lite-text-to-image-request
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"prompt": "Realistic DSLR photograph of an anthropomorphic sparrow sitting on a house roof beside a small bowl of water. The sparrow is holding a leaf in a natural pose. The text 'Seedream 5.0 Lite available on Pixazo AI' is clearly visible at the top of the image.",
"image_size": "auto_2K",
"num_images": 1,
"max_images": 1,
"enable_safety_checker": true,
"enhance_prompt_mode": "standard"
}
```
**Output**
```
{
"request_id": "seedream-5-0-lite-text-to-image_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/seedream-5-0-lite-text-to-image_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Seedream 5.0 Lite Text to Image generate request
| Parameter | Required | Type | Description |
| prompt | Yes | string | Text description for image generation. Use detailed, descriptive language for best results. |
| image_size | No | string | Target image resolution. Supported values: `square_hd`, `square`, `portrait_4_3`, `portrait_16_9`, `landscape_4_3`, `landscape_16_9`, `auto_2K`, `auto_3K`. |
| num_images | No | integer | Number of images to generate per request. Must be between 1 and 6. |
| max_images | No | integer | Maximum number of images to return. Must be equal to or greater than `num_images`. |
| enable_safety_checker | No | boolean | Enables content safety filtering to block inappropriate or harmful outputs. |
| enhance_prompt_mode | No | string | Prompt enhancement strategy. Options: `none`, `standard`, `creative`, `photorealistic`. |
**Example Request**
```
{
"prompt": "Realistic DSLR photograph of an anthropomorphic sparrow sitting on a house roof beside a small bowl of water. The sparrow is holding a leaf in a natural pose. The text 'Seedream 5.0 Lite available on Pixazo AI' is clearly visible at the top of the image.",
"image_size": "auto_2K",
"num_images": 1,
"max_images": 1,
"enable_safety_checker": true,
"enhance_prompt_mode": "standard"
}
```
**Response**
```
{
"request_id": "seedream-5-0-lite-text-to-image_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/seedream-5-0-lite-text-to-image_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```
Seedream 5.0 Lite Text to Image check status - Seedream 5.0 Lite Text to Image
**Request Code**
```
POST https://gateway.pixazo.ai/seedream-5-0-lite-text-to-image/v1/seedream-5-0-lite-text-to-image-request-result
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"request_id": "abc123-def456-7890"
}
```
**Output**
```
{
"images": [
{
"url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/seedream-5-0-lite-edit/xvbT7A5LsRoFTWSmUXCtX_e9fa2bcda22249488eb78daa1fbe401d.png",
"content_type": "image/png",
"file_name": "e9fa2bcda22249488eb78daa1fbe401d.png",
"file_size": 6361422,
"width": null,
"height": null
}
]
}
```
Request Parameters - Seedream 5.0 Lite Text to Image check status
| Parameter | Required | Type | Description |
| request_id | Yes | string | Unique identifier of the generation request to check status for. |
**Example Request**
```
{
"request_id": "abc123-def456-7890"
}
```
**Response**
```
{
"images": [
{
"url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/seedream-5-0-lite-edit/xvbT7A5LsRoFTWSmUXCtX_e9fa2bcda22249488eb78daa1fbe401d.png",
"content_type": "image/png",
"file_name": "e9fa2bcda22249488eb78daa1fbe401d.png",
"file_size": 6361422,
"width": null,
"height": null
}
]
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```
#### 2. Seedream 4.5
#### Seedream 4.5 Image To Image API Documentation
**Request Code**
```
POST https://gateway.pixazo.ai/byteplus/v1/getEditImage
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"model": "seededit-3-0-i2i-250628",
"prompt": "Make the cat eye blue",
"image": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/byteplus/1757499948018-hntkjsg9kj.jpg",
"guidance_scale": 6,
"seed": 42
}
```
**Output**
```
{
"created": 1757499942,
"data": [{
"url": "https://..../byteplus/XXXXXXXXXXXXXXX-hntkjsg9kj.jpg"
}],
"usage": {
"generated_images": 1,
"output_tokens": 4096,
"total_tokens": 4096
}
}
```
Request Parameters - Seededit Edit Image
| Parameter | Required | Type | Description |
| model | No | string | The ID of the model to call. You can activate a model service and query the model ID. An endpoint ID can also be used to call a model. |
| prompt | Yes | string | Text description (prompt) used to edit images. Describes the desired changes to be made to the input image. |
| image | Yes | string | The image to be edited. Accepts either Base64 encoding or an accessible URL. |
**Image URL**: Ensure the URL is accessible.
**Base64**: Must be in the format `data:image/;base64,` (format in lowercase, e.g., `data:image/png;base64,...`).
| response_format | No | string | Format of the returned image. |
Options: `"url"` (downloadable JPEG link), `"b64_json"` (Base64-encoded JSON string).
| size | No | string | Dimensions of the generated image. Currently only **adaptive** is supported. The system compares the input image size with predefined sizes and selects the closest match, prioritizing minimal aspect ratio differences. **For Model seedream-4-5-251128, Size supported 2K, 4k.** Total pixel range: seedream 4.5：[2560x1440=3686400, 4096x4096=16777216] |
| seed | No | integer | Random seed to control generation randomness. Range: **[-1, 2147483647]**. If `-1` or not set, a random seed is generated automatically. Use the same seed for consistent results. |
| guidance_scale | No | float | Controls how much the text prompt vs. input image influences the output. Range: **[1, 10]**. Higher values = stronger text prompt influence, weaker input image influence. |
| watermark | No | boolean | Whether to add a watermark. |
`false`: No watermark.
`true`: Adds "AI-generated" watermark in the bottom-right corner.
**Example Request**
```
{
"model": "seededit-3-0-i2i-250628",
"prompt": "Make the cat eye blue",
"image": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/byteplus/1757499948018-hntkjsg9kj.jpg",
"guidance_scale": 6,
"seed": 42
}
```
**Response**
```
{
"created": 1757499942,
"data": [{
"url": "https://..../byteplus/XXXXXXXXXXXXXXX-hntkjsg9kj.jpg"
}],
"usage": {
"generated_images": 1,
"output_tokens": 4096,
"total_tokens": 4096
}
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```
#### Seedream 4.5 Text To Image API Documentation
**Request Code**
```
POST https://gateway.pixazo.ai/byteplus/v1/getTextToImage
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"prompt": "A fisheye lens close-up of a cat’s head, where the unique distortion of the lens exaggerates and warps the cat’s facial features for a playful, dramatic effect."
}
```
**Output**
```
{
"created": 1757499942,
"data": [{
"url": "https://..../byteplus/XXXXXXXXXXXXXXX-hntkjsg9kj.jpg"
}],
"usage": {
"generated_images": 1,
"output_tokens": 4096,
"total_tokens": 4096
}
}
```
Request Parameters - Seedream Text to Image
| Parameter | Required | Type | Description |
| model | No | string | The ID of the model to call. You can activate a model service and query the model ID. An endpoint ID can also be used to call a |
| prompt | Yes | string | The text prompt used to generate the image. Describes the style and content for the generated image. |
| response_format | No | string | Specifies the format of the generated image returned in the response. Supported values: "url" (downloadable JPEG image link), "b64_json" (Base64-encoded JSON string). |
| size | No(Yes for seedream 4.5) | string | Specifies the dimensions (width x height in pixels) of the generated image. Must be between **512x512** and **2048x2048**. Recommended: 1024x1024 (1:1), 864x1152 (3:4), 1152x864 (4:3), 1280x720 (16:9), 720x1280 (9:16), 832x1248 (2:3), 1248x832 (3:2), 1512x648 (21:9). **For Model seedream-4-5-251128, Size supported 2K, 4k.** Total pixel range: seedream 4.5：[2560x1440=3686400, 4096x4096=16777216], seedream 4.0：[1280x720=921600, 4096x4096=16777216] |
| seed | No | integer | Random seed to control stochasticity. Range: **[-1, 2147483647]**. `-1` or unset means auto-generated. Use the same seed to reproduce results. |
| guidance_scale | No | float | Controls how closely the output matches the prompt. Higher values = stronger prompt adherence, less freedom. Range: **[1, 10]**. |
| watermark | No | boolean | Whether to add a watermark. `false`: No watermark. `true`: Adds "AI generated" in bottom-right corner. |
**Example Request**
```
{
"model": "seedream-3-0-t2i-250415",
"prompt": "A fisheye lens close-up of a cat’s head, where the unique distortion of the lens exaggerates and warps the cat’s facial features for a playful, dramatic effect."
}
```
**Response**
```
{
"created": 1757499942,
"data": [{
"url": "https://..../byteplus/XXXXXXXXXXXXXXX-hntkjsg9kj.jpg"
}],
"usage": {
"generated_images": 1,
"output_tokens": 4096,
"total_tokens": 4096
}
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```
#### 3. Seedream 4
#### Seedream 4 Multi Image Edit API Documentation
**Request Code**
```
POST https://gateway.pixazo.ai/byteplus/v1/getEditMultiImage
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"prompt": "Girl holding the cat",
"image": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/byteplus/1757499948018-hntkjsg9kj.jpg",
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/model.png"
]
}
```
**Output**
```
{
"created": 1757585224,
"data": [{
"url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/byteplus/1757585229985-y9119xs25t-1.jpg"
}],
"usage": {
"generated_images": 1,
"output_tokens": 4096,
"total_tokens": 4096
}
}
```
Request Parameters - Seedream Edit Multi Image
| Parameter | Required | Type | Description |
| prompt | Yes | string | Text description for image generation. Describes the desired images to be generated based on reference images. |
Auto-enhanced: When num_images > 1, the prompt is automatically enhanced to encourage multiple variations.
| image | No | string[] | Array of 1–10 reference image URLs or Base64-encoded images. Used as reference for generating related images. |
URLs: Must be accessible.
Base64: Format data:image/<format>;base64,<content>.
| response_format | No | string | Format of generated images. |
Options: "url" (downloadable JPEG links), "b64_json" (Base64-encoded JSON).
| size | No | string | Resolution of generated images. Supports various sizes including 1024x1024, 1280x720, etc. |
| watermark | No | boolean | Whether to add an AI watermark to generated images. |
Options: false (no watermark), true (adds watermark).
| sequential_image_generation | No | string | Generation mode for batch processing. |
Options: "auto" (sequential related images), "disabled" (independent generation).
Auto-override: Automatically set to "auto" when num_images > 1.
| stream | No | boolean | Whether to stream the response. *(Currently not applicable for this implementation.)* |
| num_images | No | integer | Key Parameter: Number of images to generate. When > 1, automatically enables sequential generation mode and enhances the prompt. |
*Note: Actual number may vary based on model decision and prompt complexity.*
**Example Request**
```
{
"prompt": "Girl holding the cat",
"image": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/byteplus/1757499948018-hntkjsg9kj.jpg",
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/model.png"
]
}
```
**Response**
```
{
"created": 1757585224,
"data": [{
"url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/byteplus/1757585229985-y9119xs25t-1.jpg"
}],
"usage": {
"generated_images": 1,
"output_tokens": 4096,
"total_tokens": 4096
}
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```

---

### Hunyuan 3.0 API - AI Image & 3D Model Generation APIs
**Page:** https://www.pixazo.ai/models/hunyuan


by Tencent

Hunyuan 3.0 API, developers can access Hunyuan's capabilities for generating detailed images and converting them into 3D models. The API leverages Tencent's extensive AI research to deliver high-quality outputs suitable for gaming, virtual worlds, and digital content production.

Models Version
Hunyuan v3.0
Hunyuan Image 3.0 Instruct
Text To Image
3D Generation
Text To Image
**Request Code**
**Request Parameters**
**Example Request**
**Response**
**Request Headers**
**Response Handling**
**Pricing**
3D Generation
#### Hunyuan v3.0 Text To Image API Documentation
https://gateway.pixazo.ai/hunyuan-image/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Generate image request - Hunyuan Image API
**Request Code**
```
POST https://gateway.pixazo.ai/hunyuan-image/v1/hunyuan-image/generateRequest
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"prompt": "A vibrant sunflower field at golden hour, with bees hovering above, soft wind rustling petals, ultra-detailed, photorealistic",
"negative_prompt": "blurry, cartoon, low quality, watermark",
"image_size": "landscape_16_9",
"num_images": 1,
"num_inference_steps": 28,
"guidance_scale": 7.5,
"enable_safety_checker": true,
"output_format": "png",
"enable_prompt_expansion": true
}
```
**Output**
```
{
"request_id": "hunyuan-image_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/hunyuan-image_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Generate image request
| Parameter | Required | Type | Description |
| prompt | Yes | string | The text prompt for image generation |
| negative_prompt | No | string | Default: "". The negative prompt to guide the image generation away from certain concepts |
| image_size | No | string | Default: "square_hd". The desired size of the generated image. Available values: "square_hd", "square", "portrait_4_3", "portrait_16_9", "landscape_4_3", "landscape_16_9" |
| num_images | No | integer | Default: 1. The number of images to generate. Max 4 |
| num_inference_steps | No | integer | Default: 28. Number of denoising steps. Higher values result in higher quality images but take longer to generate |
| guidance_scale | No | float | Default: 7.5. Controls how much the model adheres to the prompt. Higher values mean stricter adherence |
| seed | No | integer | Default: null. Random seed for reproducible results. If None, a random seed is used |
| enable_safety_checker | No | boolean | Default: true. Whether to enable the safety checker to filter NSFW content |
| sync_mode | No | boolean | Default: null. If true, the media will be returned as a data URI and the output data won't be available in the request history |
| output_format | No | string | Default: "png". The format of the generated image. Values: "jpeg", "png" |
| enable_prompt_expansion | No | boolean | Default: null. Whether to enable prompt expansion using a large language model to expand the prompt with additional details |
**Example Request**
```
{
"prompt": "A vibrant sunflower field at golden hour, with bees hovering above, soft wind rustling petals, ultra-detailed, photorealistic",
"negative_prompt": "blurry, cartoon, low quality, watermark",
"image_size": "landscape_16_9",
"num_images": 1,
"num_inference_steps": 28,
"guidance_scale": 7.5,
"enable_safety_checker": true,
"output_format": "png",
"enable_prompt_expansion": true
}
```
**Response**
```
{
"request_id": "hunyuan-image_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/hunyuan-image_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	Your subscription key
```
#### Hunyuan v3.0 3D Generation API Documentation
https://gateway.pixazo.ai/hunyuan3d-3-0-api-294/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Hunyuan3D 3.0 API generate request - Hunyuan 3D 3.0 API
**Request Code**
```
POST https://gateway.pixazo.ai/hunyuan3d-3-0-api-294/v1/hunyuan3d-3-0-api-request
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"input_image_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/cat-vector.png",
"prompt": "orange cat",
"face_count": 500000
}
```
**Output**
```
{
"request_id": "hunyuan3d-3-0-api-294_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/hunyuan3d-3-0-api-294_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Hunyuan3D 3.0 API generate request
| Field | Type | Required | Default | Description |
| input_image_url | string | Yes | — | URL of the input image or sketch to convert into a 3D model. Must be publicly accessible. |
| prompt | string | No | "" (empty string) | Text prompt to guide the 3D generation process. Used to refine or enhance the model when an image is provided. |
| face_count | integer | No | 500000 | Target number of polygon faces in the output 3D mesh. Higher values yield higher detail but longer processing times. |
Minimum Request
```
{
"input_image_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/cat-vector.png"
}
```
Full Request (all options)
```
{
"input_image_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/cat-vector.png",
"prompt": "orange cat",
"face_count": 500000
}
```
**Response**
```
{
"request_id": "hunyuan3d-3-0-api-294_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/hunyuan3d-3-0-api-294_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	Your subscription key
```
#### 2. Hunyuan Image 3.0 Instruct
#### Hunyuan Image 3.0 Instruct Text To Image API Documentation
https://gateway.pixazo.ai/hunyuan-image-3-0-instruct/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Hunyuan Image 3.0 Instruct generate request - Hunyuan Image 3.0 Instruct
**Request Code**
```
POST https://gateway.pixazo.ai/hunyuan-image-3-0-instruct/v1/hunyuan-image-3-0-instruct-request
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"prompt": "A detailed watercolor painting of a Japanese garden in autumn, with a red wooden bridge over a koi pond, falling maple leaves, and soft morning mist",
"image_size": "auto",
"num_images": 1,
"guidance_scale": 3.5,
"enable_safety_checker": true,
"output_format": "png"
}
```
**Output**
```
{
"request_id": "hunyuan-image-3-0-instruct_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/hunyuan-image-3-0-instruct_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Hunyuan Image 3.0 Instruct generate request
| Field | Type | Required | Default | Description |
| prompt | string | Yes | — | A detailed textual description of the desired image. Use specific visual elements, styles, lighting, and composition for best results. |
| image_size | string | No | auto | Target output resolution. Accepts "auto", "512x512", "1024x1024", "768x1344", "1344x768". |
| num_images | integer | No | 1 | Number of images to generate per request. Maximum value is 4. |
| guidance_scale | number | No | 3.5 | Controls how closely the generated image follows the prompt. Values typically range from 1.0 to 10.0. Higher values increase prompt adherence but may reduce creativity. |
| enable_safety_checker | boolean | No | true | Enables content filtering to block inappropriate or harmful outputs. Disable only if you are certain your prompts are safe. |
| output_format | string | No | png | Output image format. Accepts "png", "jpeg", or "webp". |
Minimum Request
```
{
"prompt": "A detailed watercolor painting of a Japanese garden in autumn, with a red wooden bridge over a koi pond, falling maple leaves, and soft morning mist"
}
```
Full Request (all options)
```
{
"prompt": "A detailed watercolor painting of a Japanese garden in autumn, with a red wooden bridge over a koi pond, falling maple leaves, and soft morning mist",
"image_size": "auto",
"num_images": 1,
"guidance_scale": 3.5,
"enable_safety_checker": true,
"output_format": "png"
}
```
**Response**
```
{
"request_id": "hunyuan-image-3-0-instruct_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/hunyuan-image-3-0-instruct_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	Your API subscription key
```

---

### FireRed Image Edit API - AI Image Editing APIs
**Page:** https://www.pixazo.ai/models/firered-image-edit


by FireRed Image Edit

FireRed Image Edit API, developers can access advanced editing features including intelligent modifications, style adjustments, and creative transformations. The API is designed for content creators, designers, and developers who need fast, high-quality image editing without complex workflows.

Models Version
FireRed Image Edit v1
Image Edit
Image Edit
**Request Code**
**Request Parameters**
**Example Request**
**Response**
**Request Headers**
**Response Handling**
**Pricing**
#### FireRed Image Edit v1 Image Edit API Documentation
https://gateway.pixazo.ai/firered-image-edit/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Image Edit Request - FireRed Image Edit
**Request Code**
```
POST https://gateway.pixazo.ai/firered-image-edit/v1/firered-image-edit/generate
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"prompt": "The woman's dress is changed to black",
"image": ["https://example.com/photo.jpg"]
}
```
**Output**
```
{
"request_id": "firered-image-edit_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/firered-image-edit_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Image Edit Request
| Parameter | Required | Type | Description |
| prompt | Yes | string | Text instruction describing the requested image edit |
| image | Yes | array | Input image URLs to edit or use as references. Must be jpeg, png, gif, or webp |
| seed | No | integer | Default: null. Random seed for reproducibility. Leave blank for a random seed |
| go_fast | No | boolean | Default: true. Use optimized cache scheduling for faster generation |
| aspect_ratio | No | string | Default: "match_input_image". Values: "1:1", "16:9", "9:16", "4:3", "3:4", "match_input_image" |
| output_format | No | string | Default: "webp". Output image format. Values: "webp", "jpg", "png" |
| output_quality | No | integer | Default: 95. Image quality from 0 (lowest) to 100 (highest) |
| true_cfg_scale | No | number | Default: 4. True CFG guidance scale. Valid range: 0-20. Higher values follow the prompt more closely |
| num_inference_steps | No | integer | Default: 40. Number of denoising steps. Valid range: 1-100. More steps = better quality but slower |
| webhook | No | string | Default: null. Callback URL for completion notification |
| webhook_events_filter | No | array | Default: ["*"]. Events that trigger webhook. Values: ["*"] (all), ["completed"] (success/failure only) |
**Example Request**
```
{
"prompt": "The woman's dress is changed to black",
"image": ["https://example.com/photo.jpg"],
"output_format": "png",
"output_quality": 100
}
```
**Response**
```
{
"request_id": "firered-image-edit_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/firered-image-edit_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```
Image Edit Status - FireRed Image Edit
**Request Code**
```
POST https://gateway.pixazo.ai/firered-image-edit/v1/firered-image-edit/prediction
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"prediction_id": "w974cvx49hrmy0cwdfzv180nm8"
}
```
**Output**
```
{
"success": true,
"id": "w974cvx49hrmy0cwdfzv180nm8",
"status": "succeeded",
"output": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/..."
}
```
Request Parameters - Image Edit Status
| Parameter | Required | Type | Description |
| prediction_id | Yes | string | The unique identifier returned from the initial image edit request |
**Example Request**
```
{
"prediction_id": "w974cvx49hrmy0cwdfzv180nm8"
}
```
**Response**
```
{
"success": true,
"id": "w974cvx49hrmy0cwdfzv180nm8",
"status": "succeeded",
"input": {
"prompt": "The woman's dress is changed to black",
"image": ["https://example.com/photo.jpg"],
"go_fast": true,
"aspect_ratio": "match_input_image",
"output_format": "webp",
"output_quality": 95,
"true_cfg_scale": 4,
"num_inference_steps": 40
},
"output": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/firered-image-edit/w974cvx49hrmy0cwdfzv180nm8_output_0.webp",
"created_at": "2026-02-17T14:27:21.804Z"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```

---

### P-Image API - AI Image Editing & Transformation APIs
**Page:** https://www.pixazo.ai/models/p-image


by Pruna AI

P Image by Pruna AI provides advanced AI-powered image editing and transformation capabilities. Through Pixazo's API, developers can integrate intelligent image editing features that enable precise modifications, creative transformations, and image-to-image generation. The API supports versatile editing workflows for content creators, designers, and developers seeking high-quality AI image processing.

Models Version
P Image Upscale
P Image v1
Image Edit
Image Edit
**Request Code**
**Request Parameters**
**Example Request**
**Response**
**Request Headers**
**Response Handling**
**Pricing**
#### P Image Upscale Image Edit API Documentation
https://gateway.pixazo.ai/p-image-upscale/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
P Image Upscale Request - P Image Upscale API
**Request Code**
```
POST https://gateway.pixazo.ai/p-image-upscale/v1/p-image-upscale/generate
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"image": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/f1.png"
}
```
**Output**
```
{
"request_id": "p-image-upscale_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/p-image-upscale_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - P Image Upscale Request
| Parameter | Required | Type | Description |
| image | Yes | string (URI) | Input image URL to upscale. Must be publicly accessible. |
| upscale_mode | No | string | Upscale mode: "target" (scales to fixed megapixel resolution) or "factor" (multiplies each side by factor). Default: "target". |
| target | No | integer | Target resolution in megapixels (1-8). Used when upscale_mode is "target". Default: 4. |
| factor | No | number | Scaling factor applied to each side (1-8). Used when upscale_mode is "factor". Output capped at 8 MP. Default: 2. |
| enhance_details | No | boolean | Enhance fine textures and small details. May increase contrast. Default: false. |
| enhance_realism | No | boolean | Improve realism. Recommended for AI-generated images. Default: true. |
| output_format | No | string | Output format: "webp", "jpg", or "png". Default: "jpg". |
| output_quality | No | integer | Output quality (0-100). 100 is best. Not relevant for PNG. Default: 80. |
| disable_safety_checker | No | boolean | Disable safety checker. Default: false. |
**Example Request**
```
{
"image": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/f1.png",
"upscale_mode": "target",
"target": 4,
"enhance_details": true,
"enhance_realism": true,
"output_format": "jpg",
"output_quality": 90
}
```
**Response**
```
{
"request_id": "p-image-upscale_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/p-image-upscale_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```
#### 2. P Image v1
#### P Image v1 Image Edit API Documentation
https://gateway.pixazo.ai/p-image/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Image Request - P Image
**Request Code**
```
POST https://gateway.pixazo.ai/p-image/v1/p-image-edit/generate
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_API_KEY
{
"prompt": "The woman dress is changed to black",
"images": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/f1.png"
]
}
```
**Output**
```
{
"request_id": "p-image_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/p-image_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Image Request
| Field | Type | Required | Default | Description |
| prompt | string | Yes | — | Text prompt describing the desired image edit. You can refer to images as "image 1", "image 2", etc. |
| images | array of strings (URI) | Yes | — | Input image URLs. For editing tasks, provide the main image as the first image |
| turbo | boolean | No | true | Faster generation with additional optimizations. Turn off for complicated tasks |
| seed | integer | No | — | Random seed for reproducible generation |
| aspect_ratio | string | No | "match_input_image" | Aspect ratio for the generated image. Valid values: "match_input_image", "1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3" |
| disable_safety_checker | boolean | No | false | Disable safety checker for generated images |
| webhook | string | No | — | Webhook URL for async notifications when generation completes |
| webhook_events_filter | array | No | — | Event types to receive (e.g. ["completed"]) |
Minimum Request
```
{
"prompt": "The woman dress is changed to black",
"images": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/f1.png"
]
}
```
Full Request (all options)
```
{
"prompt": "The woman dress is changed to black",
"images": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/f1.png"
],
"turbo": true,
"seed": 42,
"aspect_ratio": "1:1",
"disable_safety_checker": false,
"webhook": "https://your-webhook.com/callback",
"webhook_events_filter": [
"completed"
]
}
```
**Response**
```
{
"request_id": "p-image_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/p-image_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	Your API subscription key
```

---

### Wan 2.7 Pro API, Wan 2.6 API, Wan 2.5 API, Wan 2.2 API - AI Video & Image Generation APIs
**Page:** https://www.pixazo.ai/models/wan


by Alibaba

Wan 2.7 Pro API, developers can access multiple Wan versions (2.2, 2.5, 2.6) for text-to-video, image-to-video, speech-to-video, and image generation. The API provides extensive capabilities including animation and flash video generation, making it one of the most versatile video AI solutions available.

Models Version
Wan 2.7
Wan 2.7 Pro
Wan 2.6
Wan 2.5
Wan 2.2
Text To Image
Edit Image
Edit Video (Video to Video)
Edit Video (Video With Reference Image to Video)
Text To Image
**Request Code**
**Request Parameters**
**Example Request**
**Response**
**Request Headers**
**Response Handling**
**Pricing**
Edit Image
Edit Video (Video to Video)
Edit Video (Video With Reference Image to Video)
#### Wan 2.7 Text To Image API Documentation
https://gateway.pixazo.ai/wan-2-7-api/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Wan 2.7 Text to Image Request - Wan 2.7 API
**Request Code**
```
POST https://gateway.pixazo.ai/wan-2-7-api/v1/generateWan27TextToImageRequest
Content-Type: application/json
Ocp-Apim-Subscription-Key: YOUR_API_KEY
{
"prompt": "A charming flower shop with beautiful window displays filled with colorful flowers"
}
```
**Output**
```
{
"status": "QUEUED",
"request_id": "wan-2-7-api_019d4e3f-83c5-719c-dbba-039df43078184",
"message": "Request accepted and queued for processing"
}
```
**Webhook (Optional)**

You can optionally provide a webhook_url in your request body. When the request completes (success or failure), a POST request will be sent to your webhook URL with the result payload.

Webhook Payload (Success)
```
{
"request_id": "wan-2-7-api_019d4e3f-83c5-719c-dbba-039df43078184",
"status": "COMPLETED",
"result": {
"images": [
{
"file_name": "output.png",
"content_type": "image/png",
"url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/v1/wan-2-7-api_019d4e3f-83c5-719c-dbba-039df43078184/output.png"
}
],
"description": ""
}
}
```
Webhook Payload (Failure)
```
{
"request_id": "wan-2-7-api_019d4e3f-83c5-719c-dbba-039df43078184",
"status": "FAILED",
"error": "Processing failed: upstream provider returned an error"
}
```
Request Parameters - Wan 2.7 Text to Image Request
| Field | Type | Required | Default | Description |
| prompt | string | Yes | — | Text description of the image to generate. Supports Chinese and English, max 5000 characters. |
| size | string | No | "2K" | Output resolution: "1K", "2K" (default). No 4K for standard model. |
| n | integer | No | 4 | Number of images to generate, 1-4. |
| thinking_mode | boolean | No | true | Enables thinking mode for better quality. Increases generation time. |
| watermark | boolean | No | false | Adds "AI Generated" watermark. |
| seed | integer | No | — | Random seed [0, 2147483647]. Same seed yields similar outputs. |
| enable_sequential | boolean | No | false | Enables image set output mode. |
| color_palette | array | No | — | Custom color theme. Array of objects with hex (string) and ratio (string, e.g. "25.00%"). 3-10 colors. |
| webhook_url | string | No | — | URL to receive a POST callback when the request completes. Must be a publicly accessible HTTPS endpoint. |
Minimum Request
```
{
"prompt": "A charming flower shop with beautiful window displays filled with colorful flowers"
}
```
Full Request (all options)
```
{
"prompt": "A charming flower shop with beautiful window displays filled with colorful flowers",
"size": "2K",
"n": 2,
"thinking_mode": true,
"watermark": false,
"seed": 12345
}
```
**Response**
```
{
"status": "QUEUED",
"request_id": "wan-2-7-api_019d4e3f-83c5-719c-dbba-039df43078184",
"message": "Request accepted and queued for processing"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Ocp-Apim-Subscription-Key	Your API subscription key
```
#### Wan 2.7 Edit Image API Documentation
https://gateway.pixazo.ai/wan-2-7-api/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Wan 2.7 Edit Image Request - Wan 2.7 API
**Request Code**
```
POST https://gateway.pixazo.ai/wan-2-7-api/v1/generateWan27EditImageRequest
Content-Type: application/json
Ocp-Apim-Subscription-Key: YOUR_API_KEY
{
"images": ["https://pub-582b7213209642b9b995c96c95a30381.r2.dev/f1.png"],
"prompt": "Transform the tree into a lush green tree with vibrant flowers"
}
```
**Output**
```
{
"status": "QUEUED",
"request_id": "wan-2-7-api_019d4e44-7a8e-7df1-fad0-926fa428ca293",
"message": "Request accepted and queued for processing"
}
```
**Webhook (Optional)**

You can optionally provide a webhook_url in your request body. When the request completes (success or failure), a POST request will be sent to your webhook URL with the result payload.

Webhook Payload (Success)
```
{
"request_id": "wan-2-7-api_019d4e44-7a8e-7df1-fad0-926fa428ca293",
"status": "COMPLETED",
"result": {
"images": [
{
"file_name": "output.png",
"content_type": "image/png",
"url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/v1/wan-2-7-api_019d4e44-7a8e-7df1-fad0-926fa428ca293/output.png"
}
],
"description": ""
}
}
```
Webhook Payload (Failure)
```
{
"request_id": "wan-2-7-api_019d4e44-7a8e-7df1-fad0-926fa428ca293",
"status": "FAILED",
"error": "Processing failed: upstream provider returned an error"
}
```
Request Parameters - Wan 2.7 Edit Image Request
| Field | Type | Required | Default | Description |
| images | array | Yes | — | Array of image URLs to edit. Supports 1-9 images. URLs must be publicly accessible. Supported formats: JPEG, JPG, PNG, BMP, WEBP. Max 20MB per image. Resolution: 240-8000px, aspect ratio 1:8 to 8:1. |
| prompt | string | Yes | — | Text description of the desired edit. Max 5000 characters. |
| size | string | No | "2K" | Output resolution: "1K", "2K" (default). |
| n | integer | No | 4 | Number of images to generate, 1-4. |
| watermark | boolean | No | false | Adds "AI Generated" watermark. |
| seed | integer | No | — | Random seed [0, 2147483647]. |
| bbox_list | array | No | — | Selected areas for interactive editing. Array of arrays matching input image count. Each image supports up to 2 bounding boxes as [x1, y1, x2, y2] pixel coordinates. |
| enable_sequential | boolean | No | false | Enables image set output mode. |
| webhook_url | string | No | — | URL to receive a POST callback when the request completes. Must be a publicly accessible HTTPS endpoint. |
Minimum Request
```
{
"images": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/f1.png"
],
"prompt": "Transform the tree into a lush green tree with vibrant flowers"
}
```
Full Request (all options)
```
{
"images": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/f1.png"
],
"prompt": "Transform the tree into a lush green tree with vibrant flowers",
"size": "2K",
"n": 1,
"watermark": false,
"seed": 42
}
```
**Response**
```
{
"status": "QUEUED",
"request_id": "wan-2-7-api_019d4e44-7a8e-7df1-fad0-926fa428ca293",
"message": "Request accepted and queued for processing"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Ocp-Apim-Subscription-Key	Your API subscription key
```
#### Wan 2.7 Edit Video (Video to Video) API Documentation
https://gateway.pixazo.ai/wan-2-7-video-api/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Wan 2.7 Video Style Transfer Request - Wan 2.7 Video API
**Request Code**
```
POST https://gateway.pixazo.ai/wan-2-7-video-api/v1/generateWan27VideoStyleRequest
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"video_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/wan-t2v/wan-t2v-6421dc79-417c-445c-96e3-65c333aeafb9.mp4",
"prompt": "Convert to claymation style"
}
```
**Output**
```
{
"request_id": "wan-2-7-video-api_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/wan-2-7-video-api_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Wan 2.7 Video Style Transfer Request
| Parameter | Required | Type | Description |
| video_url | Yes | string | URL of the video to edit. Format: MP4, MOV. Duration: 2-10s. Max 100MB. |
| reference_images | Yes | array | Array of reference image URLs (1-3 images). Format: JPEG, JPG, PNG, BMP, WEBP. Max 20MB each. |
| prompt | Yes | string | Text description of the desired edit. Max 5,000 characters. |
| negative_prompt | No | string | Describes content you do not want in the video. Max 500 characters. |
| resolution | No | string | Resolution of the output video. Supported: "720P", "1080P". Default: "1080P". |
| ratio | No | string | Aspect ratio. Supported: "16:9", "9:16", "1:1", "4:3", "3:4". |
| duration | No | integer | Duration in seconds. Set only to truncate. Range: 2-10. |
| audio_setting | No | string | "auto" (model decides) or "origin" (retains original audio). Default: "auto". |
| prompt_extend | No | boolean | Enables prompt rewriting using LLM. Default: true. |
| watermark | No | boolean | Whether to add "AI-generated" watermark. Default: false. |
| seed | No | integer | Random seed for reproducible results. Range: 0-2147483647. |
**Example Request**
```
{
"video_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/wan-t2v/wan-t2v-6421dc79-417c-445c-96e3-65c333aeafb9.mp4",
"prompt": "Convert to claymation style",
"negative_prompt": "low quality, blurry",
"resolution": "1080P",
"ratio": "16:9",
"duration": 5,
"audio_setting": "origin",
"prompt_extend": false,
"watermark": true,
"seed": 12345
}
```
**Response**
```
{
"request_id": "wan-2-7-video-api_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/wan-2-7-video-api_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```
#### Wan 2.7 Edit Video (Video With Reference Image to Video) API Documentation
https://gateway.pixazo.ai/wan-2-7-video-api/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Wan 2.7 Video Edit By Reference Request - Wan 2.7 Video API
**Request Code**
```
POST https://gateway.pixazo.ai/wan-2-7-video-api/v1/generateWan27VideoEditRequest
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"video_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/wan2.7-video.mp4",
"reference_images": ["https://pub-582b7213209642b9b995c96c95a30381.r2.dev/wan2.7-videoedit-change-clothes.png"],
"prompt": "Replace the clothes with the ones from the image"
}
```
**Output**
```
{
"request_id": "wan-2-7-video-api_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/wan-2-7-video-api_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Wan 2.7 Video Edit By Reference Request
| Parameter | Required | Type | Description |
| video_url | Yes | string | URL of the video to edit. Format: MP4, MOV. Duration: 2-10s. Max 100MB. |
| reference_images | Yes | array | Array of reference image URLs (1-3 images). Format: JPEG, JPG, PNG, BMP, WEBP. Max 20MB each. |
| prompt | Yes | string | Text description of the desired edit. Max 5,000 characters. |
| negative_prompt | No | string | Describes content you do not want in the video. Max 500 characters. |
| resolution | No | string | Resolution of the output video. Supported: "720P", "1080P". Default: "1080P". |
| ratio | No | string | Aspect ratio. Supported: "16:9", "9:16", "1:1", "4:3", "3:4". |
| duration | No | integer | Duration in seconds. Set only to truncate. Range: 2-10. |
| audio_setting | No | string | "auto" (model decides) or "origin" (retains original audio). Default: "auto". |
| prompt_extend | No | boolean | Enables prompt rewriting using LLM. Default: true. |
| watermark | No | boolean | Whether to add "AI-generated" watermark. Default: false. |
| seed | No | integer | Random seed for reproducible results. Range: 0-2147483647. |
**Example Request**
```
{
"video_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/wan2.7-video.mp4",
"reference_images": ["https://pub-582b7213209642b9b995c96c95a30381.r2.dev/wan2.7-videoedit-change-clothes.png"],
"prompt": "Replace the clothes with the ones from the image",
"negative_prompt": "low quality, blurry",
"resolution": "1080P",
"ratio": "16:9",
"duration": 5,
"audio_setting": "origin",
"prompt_extend": false,
"watermark": true,
"seed": 12345
}
```
**Response**
```
{
"request_id": "wan-2-7-video-api_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/wan-2-7-video-api_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```
#### 2. Wan 2.7 Pro
#### Wan 2.7 Pro Text To Image API Documentation
https://gateway.pixazo.ai/wan-2-7-pro-api/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Wan 2.7 Pro Text to Image Request - Wan 2.7 Pro API
**Request Code**
```
POST https://gateway.pixazo.ai/wan-2-7-pro-api/v1/generateWan27ProTextToImageRequest
Content-Type: application/json
Ocp-Apim-Subscription-Key: YOUR_API_KEY
{
"prompt": "A charming flower shop with beautiful window displays filled with colorful flowers"
}
```
**Output**
```
{
"status": "QUEUED",
"request_id": "wan-2-7-pro-api_019d4e4b-b2da-7a82-ecae-994db4acea2c6",
"message": "Request accepted and queued for processing"
}
```
**Webhook (Optional)**

You can optionally provide a webhook_url in your request body. When the request completes (success or failure), a POST request will be sent to your webhook URL with the result payload.

Webhook Payload (Success)
```
{
"request_id": "wan-2-7-pro-api_019d4e4b-b2da-7a82-ecae-994db4acea2c6",
"status": "COMPLETED",
"result": {
"images": [
{
"file_name": "output.png",
"content_type": "image/png",
"url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/v1/wan-2-7-pro-api_019d4e4b-b2da-7a82-ecae-994db4acea2c6/output.png"
}
],
"description": ""
}
}
```
Webhook Payload (Failure)
```
{
"request_id": "wan-2-7-pro-api_019d4e4b-b2da-7a82-ecae-994db4acea2c6",
"status": "FAILED",
"error": "Processing failed: upstream provider returned an error"
}
```
Request Parameters - Wan 2.7 Pro Text to Image Request
| Field | Type | Required | Default | Description |
| prompt | string | Yes | — | Text description of the image to generate. Supports Chinese and English, max 5000 characters. |
| size | string | No | "2K" | Output resolution: "1K", "2K" (default), "4K". |
| n | integer | No | 4 | Number of images to generate, 1-4. |
| thinking_mode | boolean | No | true | Enables thinking mode for better quality. Increases generation time. |
| watermark | boolean | No | false | Adds "AI Generated" watermark. |
| seed | integer | No | — | Random seed [0, 2147483647]. Same seed yields similar outputs. |
| enable_sequential | boolean | No | false | Enables image set output mode. |
| color_palette | array | No | — | Custom color theme. Array of objects with hex (string) and ratio (string, e.g. "25.00%"). 3-10 colors. |
| webhook_url | string | No | — | URL to receive a POST callback when the request completes. Must be a publicly accessible HTTPS endpoint. |
Minimum Request
```
{
"prompt": "A charming flower shop with beautiful window displays filled with colorful flowers"
}
```
Full Request (all options)
```
{
"prompt": "A charming flower shop with beautiful window displays filled with colorful flowers",
"size": "2K",
"n": 2,
"thinking_mode": true,
"watermark": false,
"seed": 12345
}
```
**Response**
```
{
"status": "QUEUED",
"request_id": "wan-2-7-pro-api_019d4e4b-b2da-7a82-ecae-994db4acea2c6",
"message": "Request accepted and queued for processing"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Ocp-Apim-Subscription-Key	Your API subscription key
```
#### Wan 2.7 Pro Edit Image API Documentation
https://gateway.pixazo.ai/wan-2-7-pro-api/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Wan 2.7 Pro Edit Image Request - Wan 2.7 Pro API
**Request Code**
```
POST https://gateway.pixazo.ai/wan-2-7-pro-api/v1/generateWan27ProEditImageRequest
Content-Type: application/json
Ocp-Apim-Subscription-Key: YOUR_API_KEY
{
"images": ["https://pub-582b7213209642b9b995c96c95a30381.r2.dev/f1.png"],
"prompt": "Transform the tree into a lush green tree with vibrant flowers"
}
```
**Output**
```
{
"status": "QUEUED",
"request_id": "wan-2-7-pro-api_019d4e48-893e-7582-850a-9be6943bebe13",
"message": "Request accepted and queued for processing"
}
```
**Webhook (Optional)**

You can optionally provide a webhook_url in your request body. When the request completes (success or failure), a POST request will be sent to your webhook URL with the result payload.

Webhook Payload (Success)
```
{
"request_id": "wan-2-7-pro-api_019d4e48-893e-7582-850a-9be6943bebe13",
"status": "COMPLETED",
"result": {
"images": [
{
"file_name": "output.png",
"content_type": "image/png",
"url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/v1/wan-2-7-pro-api_019d4e48-893e-7582-850a-9be6943bebe13/output.png"
}
],
"description": ""
}
}
```
Webhook Payload (Failure)
```
{
"request_id": "wan-2-7-pro-api_019d4e48-893e-7582-850a-9be6943bebe13",
"status": "FAILED",
"error": "Processing failed: upstream provider returned an error"
}
```
Request Parameters - Wan 2.7 Pro Edit Image Request
| Field | Type | Required | Default | Description |
| images | array | Yes | — | Array of image URLs to edit. Supports 1-9 images. URLs must be publicly accessible. Supported formats: JPEG, JPG, PNG, BMP, WEBP. Max 20MB per image. Resolution: 240-8000px, aspect ratio 1:8 to 8:1. |
| prompt | string | Yes | — | Text description of the desired edit. Max 5000 characters. |
| size | string | No | "2K" | Output resolution: "1K", "2K" (default). Note: 4K is only available for text-to-image. |
| n | integer | No | 4 | Number of images to generate, 1-4. |
| watermark | boolean | No | false | Adds "AI Generated" watermark. |
| seed | integer | No | — | Random seed [0, 2147483647]. |
| bbox_list | array | No | — | Selected areas for interactive editing. Array of arrays matching input image count. Each image supports up to 2 bounding boxes as [x1, y1, x2, y2] pixel coordinates. |
| enable_sequential | boolean | No | false | Enables image set output mode. |
| webhook_url | string | No | — | URL to receive a POST callback when the request completes. Must be a publicly accessible HTTPS endpoint. |
Minimum Request
```
{
"images": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/f1.png"
],
"prompt": "Transform the tree into a lush green tree with vibrant flowers"
}
```
Full Request (all options)
```
{
"images": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/f1.png"
],
"prompt": "Transform the tree into a lush green tree with vibrant flowers",
"size": "2K",
"n": 1,
"watermark": false,
"seed": 42
}
```
**Response**
```
{
"status": "QUEUED",
"request_id": "wan-2-7-pro-api_019d4e48-893e-7582-850a-9be6943bebe13",
"message": "Request accepted and queued for processing"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Ocp-Apim-Subscription-Key	Your API subscription key
```
#### 3. Wan 2.6
#### Wan 2.6 Image To Video API Documentation
https://gateway.pixazo.ai/wan-2-6-image-to-video-477/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Wan 2.6 Image to Video generate request - Wan 2.6 Image to Video API
**Request Code**
```
POST https://gateway.pixazo.ai/wan-2-6-image-to-video-477/v1/wan-2-6-image-to-video-request
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"prompt": "A serene mountain lake at sunrise, with gently flowing water and birds flying overhead",
"image_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/f1.png",
"aspect_ratio": "16:9",
"resolution": "1080p",
"duration": "10",
"enable_prompt_expansion": true,
"multi_shots": true,
"enable_safety_checker": true
}
```
**Output**
```
{
"request_id": "wan-2-6-image-to-video-477_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/wan-2-6-image-to-video-477_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Wan 2.6 Image to Video generate request
| Parameter | Required | Type | Description |
| prompt | Yes | string | Text prompt describing the desired motion, mood, or style for the generated video. Enhances visual storytelling. |
| image_url | Yes | string | Publicly accessible URL of the source image to convert into video. Must be reachable by the API server. |
| aspect_ratio | No | string | Aspect ratio of the output video. Supported values: "16:9", "9:16", "1:1", "4:3", "3:4". |
| resolution | No | string | Resolution of the output video. Supported values: "720p", "1080p". |
| duration | No | string | Duration of the generated video in seconds. Supported values: "5", "10", "15". |
| enable_prompt_expansion | No | boolean | When true, the API will enhance and expand the provided prompt for richer, more descriptive video generation. |
| multi_shots | No | boolean | When true, enables multiple camera shots and transitions within the video for dynamic, cinematic output. |
| enable_safety_checker | No | boolean | When true, activates content safety filtering to block inappropriate or harmful outputs. |
**Example Request**
```
{
"prompt": "A serene mountain lake at sunrise, with gently flowing water and birds flying overhead",
"image_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/f1.png",
"aspect_ratio": "16:9",
"resolution": "1080p",
"duration": "10",
"enable_prompt_expansion": true,
"multi_shots": true,
"enable_safety_checker": true
}
```
**Response**
```
{
"request_id": "wan-2-6-image-to-video-477_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/wan-2-6-image-to-video-477_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```
#### Wan 2.6 Text To Video API Documentation
https://gateway.pixazo.ai/wan-2-6-text-to-video-569/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Wan 2.6 Text to Video generate request - Wan 2.6 Text to Video API
**Request Code**
```
POST https://gateway.pixazo.ai/wan-2-6-text-to-video-569/v1/wan-2-6-text-to-video-request
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"prompt": "Create a cinematic video with multiple scenes showing a fox director making a movie, transitioning between different environments.",
"aspect_ratio": "16:9",
"resolution": "1080p",
"duration": "10",
"negative_prompt": "low resolution, error, worst quality, low quality, defects",
"enable_prompt_expansion": true,
"multi_shots": true,
"enable_safety_checker": true
}
```
**Output**
```
{
"request_id": "wan-2-6-text-to-video-569_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/wan-2-6-text-to-video-569_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Wan 2.6 Text to Video generate request
| Parameter | Required | Type | Description |
| prompt | Yes | string | A detailed text description of the desired video content. Include scenes, actions, lighting, and mood for optimal results. |
| aspect_ratio | No | string | The aspect ratio of the output video. Supported values: "16:9", "9:16", "1:1", "4:3", "3:4". |
| resolution | No | string | The output video resolution. Supported values: "720p", "1080p". |
| duration | No | string | The length of the generated video in seconds. Supported values: "5", "10", "15". |
| negative_prompt | No | string | Description of elements to avoid in the video. Helps improve output quality by excluding undesired visuals. |
| enable_prompt_expansion | No | boolean | Enables intelligent prompting expansion to enhance scene richness and detail. When enabled, the model may augment your prompt with contextual enhancements. |
| multi_shots | No | boolean | Enables multi-shot generation, allowing the model to segment the video into multiple cohesive scenes based on the prompt. |
| enable_safety_checker | No | boolean | Activates content safety filtering to block inappropriate or harmful outputs. |
**Example Request**
```
{
"prompt": "Create a cinematic video with multiple scenes showing a fox director making a movie, transitioning between different environments.",
"aspect_ratio": "16:9",
"resolution": "1080p",
"duration": "10",
"negative_prompt": "low resolution, error, worst quality, low quality, defects",
"enable_prompt_expansion": true,
"multi_shots": true,
"enable_safety_checker": true
}
```
**Response**
```
{
"status": "IN_QUEUE",
"request_id": "a1b2c3d4-e5f6-7890-g1h2-i3j4k5l6m7n8",
"response_url": "[RESPONSE_URL]"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```
#### Wan 2.6 Image To Video(Flash) API Documentation
https://gateway.pixazo.ai/wan-2-6-image-to-video-flash-api-353/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Wan 2.6 Image-to-Video Flash API generate request - Wan 2.6 Image-to-Video Flash API
**Request Code**
```
POST https://gateway.pixazo.ai/wan-2-6-image-to-video-flash-api-353/v1/wan-2-6-image-to-video-flash-api-request
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"prompt": "A cinematic beach portrait that evolves into an energy-charged scene. Photorealistic, stable identity matching the reference image, natural lighting, smooth cinematic motion, no subtitles.\n\nShot 1 [0-4s] Continue from the first frame of the reference image. The man stands on a sunny beach wearing a black t-shirt with glowing blue flame graphics. Gentle ocean waves move behind him and wind lightly moves his hair and shirt. The blue flame design on the shirt begins to softly glow and flicker like living energy.\n\nShot 2 [4-8s] Slow cinematic push-in toward the subject. The glowing blue flames on the shirt animate and flow upward like magical energy. Subtle particles and light streaks appear around the flame pattern. The ocean sparkles in the sunlight while waves roll naturally in the background.\n\nShot 3 [8-12s] Hard cinematic cut to a slightly closer angle. The blue flames briefly expand outward as luminous energy patterns around the torso before settling back onto the shirt. The wind becomes slightly stronger, moving the shirt fabric and hair while the beach environment remains realistic.\n\nShot 4 [12-15s] Final cinematic close shot. The flames stabilize into a calm glowing pattern on the shirt while the subject stands confidently against the horizon. The camera slowly drifts sideways with warm sunlight reflecting off the ocean, ending in a clean photorealistic frame.",
"image_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/input_model.png"
}
```
**Output**
```
{
"request_id": "wan-2-6-image-to-video-flash-api-353_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/wan-2-6-image-to-video-flash-api-353_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Wan 2.6 Image-to-Video Flash API generate request
| Field | Type | Required | Default | Description |
| prompt | string | Yes | — | A detailed narrative describing the desired video sequence, including shot-by-shot instructions, timing cues, and emotional tone. Must specify transitions, camera movement, and scene changes for best results. |
| image_url | string | Yes | — | Publicly accessible URL of the input image to animate. Must be a stable, direct link (e.g., PNG, JPG). Supports HTTPS. |
| resolution | string | No | 1080p | Output video resolution. Supported values: "720p", "1080p", "2160p". |
| duration | string | No | 5 | Desired video duration in seconds. Accepts integer values from 5 to 15. Longer durations may incur higher processing time. |
| negative_prompt | string | No | "" | A description of elements to avoid in the generated video. Helps exclude artifacts, distortions, or unwanted visual elements. |
| enable_prompt_expansion | boolean | No | true | When true, the model enhances the provided prompt with contextual details for richer output. Disable if you require strict prompt adherence. |
| enable_safety_checker | boolean | No | true | When true, enables content safety filtering to block inappropriate or harmful output. Disable only in controlled environments. |
Minimum Request
```
{
"prompt": "A cinematic beach portrait that evolves into an energy-charged scene. Photorealistic, stable identity matching the reference image, natural lighting, smooth cinematic motion, no subtitles.\n\nShot 1 [0-4s] Continue from the first frame of the reference image. The man stands on a sunny beach wearing a black t-shirt with glowing blue flame graphics. Gentle ocean waves move behind him and wind lightly moves his hair and shirt. The blue flame design on the shirt begins to softly glow and flicker like living energy.\n\nShot 2 [4-8s] Slow cinematic push-in toward the subject. The glowing blue flames on the shirt animate and flow upward like magical energy. Subtle particles and light streaks appear around the flame pattern. The ocean sparkles in the sunlight while waves roll naturally in the background.\n\nShot 3 [8-12s] Hard cinematic cut to a slightly closer angle. The blue flames briefly expand outward as luminous energy patterns around the torso before settling back onto the shirt. The wind becomes slightly stronger, moving the shirt fabric and hair while the beach environment remains realistic.\n\nShot 4 [12-15s] Final cinematic close shot. The flames stabilize into a calm glowing pattern on the shirt while the subject stands confidently against the horizon. The camera slowly drifts sideways with warm sunlight reflecting off the ocean, ending in a clean photorealistic frame.",
"image_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/input_model.png"
}
```
Full Request (all options)
```
{
"prompt": "A cinematic beach portrait that evolves into an energy-charged scene. Photorealistic, stable identity matching the reference image, natural lighting, smooth cinematic motion, no subtitles.\n\nShot 1 [0-4s] Continue from the first frame of the reference image. The man stands on a sunny beach wearing a black t-shirt with glowing blue flame graphics. Gentle ocean waves move behind him and wind lightly moves his hair and shirt. The blue flame design on the shirt begins to softly glow and flicker like living energy.\n\nShot 2 [4-8s] Slow cinematic push-in toward the subject. The glowing blue flames on the shirt animate and flow upward like magical energy. Subtle particles and light streaks appear around the flame pattern. The ocean sparkles in the sunlight while waves roll naturally in the background.\n\nShot 3 [8-12s] Hard cinematic cut to a slightly closer angle. The blue flames briefly expand outward as luminous energy patterns around the torso before settling back onto the shirt. The wind becomes slightly stronger, moving the shirt fabric and hair while the beach environment remains realistic.\n\nShot 4 [12-15s] Final cinematic close shot. The flames stabilize into a calm glowing pattern on the shirt while the subject stands confidently against the horizon. The camera slowly drifts sideways with warm sunlight reflecting off the ocean, ending in a clean photorealistic frame.",
"image_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/input_model.png",
"resolution": "1080p",
"duration": "15",
"negative_prompt": "low resolution, error, worst quality, low quality, defects, distorted face, extra limbs, flickering, unstable identity",
"enable_prompt_expansion": true,
"enable_safety_checker": true
}
```
**Response**
#### 4. Wan 2.5
#### Wan 2.5 Image To Image API Documentation
https://gateway.pixazo.ai/wan-image-2-5/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Generate Image to Image - Wan 2.5 Image Generation
**Request Code**
```
POST https://gateway.pixazo.ai/wan-image-2-5/v1/generateEditImage2-5Request
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"prompt": "Replace the floral dress with a vintage-style lace gown that has delicate embroidery on the collar and cuffs",
"images": [
"https://example.com/images/woman-in-dress.jpg"
],
"size": "1280*1280"
}
```
**Output**
```
{
"request_id": "wan-2-5-image-generation_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/wan-2-5-image-generation_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Generate Image to Image
| Parameter | Required | Type | Description |
| prompt | Yes | string | Text description of the editing operation to perform. |
| images | Yes | array | Array of image URLs. Single image for editing, multiple images for fusion. |
| negative_prompt | No | string | Default: null. Elements to exclude from the edited image. |
| size | No | string | Default: "1280*1280". Output image dimensions in width*height format. |
| n | No | integer | Default: 1. Number of images to generate. Currently only 1 is supported. |
| seed | No | integer | Random seed for reproducible results |
**Example Request**
```
{
"prompt": "Replace the floral dress with a vintage-style lace gown that has delicate embroidery on the collar and cuffs",
"images": [
"https://example.com/images/woman-in-dress.jpg"
],
"size": "1280*1280"
}
```
**Response**
```
{
"request_id": "wan-2-5-image-generation_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/wan-2-5-image-generation_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```
#### Wan 2.5 Text To Image API Documentation
https://gateway.pixazo.ai/wan-image-2-5/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Generate Text to Image - Wan 2.5 Image Generation
**Request Code**
```
POST https://gateway.pixazo.ai/wan-image-2-5/v1/generateTextToImage2-5Request
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"prompt": "A beautiful flower shop with exquisite windows, a beautiful wooden door, and flowers on display",
"size": "1024*1024",
"prompt_extend": true,
"watermark": false
}
```
**Output**
```
{
"request_id": "wan-2-5-image-generation_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/wan-2-5-image-generation_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Generate Text to Image
| Parameter | Required | Type | Description |
| prompt | Yes | string | Text description of the image to generate. Supports English and Chinese. |
| negative_prompt | No | string | Default: null. Elements to exclude from the image. |
| size | No | string | Default: "1024*1024". Image dimensions in width*height format. |
| n | No | integer | Default: 1. Number of images to generate. Currently only 1 is supported. |
| prompt_extend | No | boolean | Default: false. Enable intelligent prompt rewriting |
| watermark | No | boolean | Default: false. Add watermark to image |
| seed | No | integer | Random seed for reproducible results |
**Example Request**
```
{
"prompt": "A majestic mountain landscape at sunset, snow-capped peaks, golden light, photorealistic",
"size": "1440*960",
"prompt_extend": true,
"watermark": false
}
```
**Response**
```
{
"request_id": "wan-2-5-image-generation_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/wan-2-5-image-generation_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```
#### Wan 2.5 Image To Video API Documentation
https://gateway.pixazo.ai/wan-video-2-5/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Generate Image To Video - Wan 2.5 Video Generation API
**Request Code**
```
POST https://gateway.pixazo.ai/wan-video-2-5/v1/generateImageToVideo2-5Request
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"img_url": "https://example.com/images/cat.png",
"prompt": "A cat running on the grass",
"resolution": "480P",
"duration": 5,
"audio": false,
"prompt_extend": true,
"watermark": false
}
```
**Output**
```
{
"request_id": "wan-image-to-video-2-5_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/wan-image-to-video-2-5_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Generate Image To Video
| Parameter | Required | Type | Description |
| img_url | Yes | string | URL to the first-frame image. Supports public URLs, Base64 encoding, or local file paths. |
| prompt | No | string | Text description of the video to generate. Supports English and Chinese. |
| negative_prompt | No | string | Default: null. Elements to exclude from the video. |
| audio_url | No | string | URL to custom audio file (overrides audio parameter). Takes priority over `audio` setting. |
| resolution | No | string | Default: "480P". Available resolutions: "480P", "720P", "1080P". Internally converted to size format. |
| duration | No | integer | Default: 5. Video length in seconds. Available values: 5, 10 |
| audio | No | boolean | Default: false. Audio behavior: false (silent), true (auto-generate audio) |
| prompt_extend | No | boolean | Default: true. Enable intelligent prompt rewriting |
| watermark | No | boolean | Default: false. Add watermark to video |
| seed | No | integer | Random seed for reproducible results |
**Example Request**
```
{
"img_url": "https://example.com/images/battle-scene.png",
"prompt": "An epic battle scene with dramatic music and sound effects",
"negative_prompt": "blurry, low quality, distorted",
"resolution": "1080P",
"duration": 10,
"audio": true,
"prompt_extend": true,
"watermark": false,
"seed": 98765
}
```
**Response**
```
{
"request_id": "wan-image-to-video-2-5_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/wan-image-to-video-2-5_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```
#### Wan 2.5 Text To Video API Documentation
https://gateway.pixazo.ai/wan-video-2-5/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Generate Text To Video Request - Wan 2.5 Video Generation API
**Request Code**
```
POST https://gateway.pixazo.ai/wan-video-2-5/v1/generateTextToVideo2-5Request
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"prompt": "A beautiful sunset over a calm ocean with gentle waves",
"size": "832*480",
"duration": 5,
"audio": false,
"prompt_extend": true,
"watermark": false
}
```
**Output**
```
{
"request_id": "wan-image-to-video-2-5_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/wan-image-to-video-2-5_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Generate Text To Video Request
| Parameter | Required | Type | Description |
| prompt | Yes | string | Text description of the video to generate. Supports English and Chinese. |
| negative_prompt | No | string | Default: null. Elements to exclude from the video. |
| audio_url | No | string | URL to custom audio file (overrides audio parameter). Takes priority over `audio` setting. |
| size | No | string | Default: "832*480". Available resolutions: "832*480", "1280*720", "1920*1080" |
| duration | No | integer | Default: 5. Video length in seconds. Available values: 5, 10 |
| audio | No | boolean | Default: false. Audio behavior: false (silent), true (auto-generate audio) |
| prompt_extend | No | boolean | Default: true. Enable intelligent prompt rewriting |
| watermark | No | boolean | Default: false. Add watermark to video |
| seed | No | integer | Random seed for reproducible results |
**Example Request**
```
{
"prompt": "A beautiful sunset over a calm ocean with gentle waves",
"size": "832*480",
"duration": 5,
"audio": false,
"prompt_extend": true,
"watermark": false
}
```
**Response**
```
{
"request_id": "wan-image-to-video-2-5_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/wan-image-to-video-2-5_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```
#### Wan 2.5 Pixazo Image To Video API Documentation
https://gateway.pixazo.ai/pixazo-wan-image-to-video-1763709522/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Generate Request - Pixazo Wan Image to Video API
**Request Code**
```
POST https://gateway.pixazo.ai/pixazo-wan-image-to-video-1763709522/v1/pixazo-wan-image-to-video-request
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"prompt": "A stylish man walks down a sea side",
"image_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/input_model.png"
}
```
**Output**
```
{
"request_id": "pixazo-wan-image-to-video-1763709522_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/pixazo-wan-image-to-video-1763709522_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Generate Request
| Field | Type | Required | Default | Description |
| prompt | string | Yes | — | A detailed text description of the desired motion and scene context. The model uses this to animate the image. |
| image_url | string | Yes | — | Publicly accessible HTTPS URL pointing to a static image (JPEG, PNG, WebP). The image will be animated according to the prompt. |
Minimum Request
```
{
"prompt": "A stylish man walks down a sea side",
"image_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/input_model.png"
}
```
Full Request (all options)
```
{
"prompt": "A stylish man walks down a sea side",
"image_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/input_model.png"
}
```
**Response**
```
{
"request_id": "pixazo-wan-image-to-video-1763709522_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/pixazo-wan-image-to-video-1763709522_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	Your subscription key
```
#### 5. Wan 2.2
#### Wan 2.2 Speech To Video API Documentation
https://gateway.pixazo.ai/wan2.2-s2v/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Speech to Video Request - Wan 2.2 14B Speech to Video
**Request Code**
```
POST https://gateway.pixazo.ai/wan2.2-s2v/v1/generateSpeechToVideoRequest
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"prompt": "Summer beach vacation style, a man wearing sunglasses Blue Tshirt.",
"image_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/input_model.png",
"audio_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/input_music.mp3"
}
```
**Output**
```
{
"request_id": "wan-2-2-14b-speech-to-video_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/wan-2-2-14b-speech-to-video_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Speech to Video Request
| Parameter | Required | Type | Description |
| prompt | Yes | string | The text prompt used for video generation. Describes the style and content for the generated video. |
| image_url | Yes | string | URL of the input image. If the input image does not match the chosen aspect ratio, it is resized and center cropped. |
| audio_url | Yes | string | The URL of the audio file that will be used to generate lip-sync and facial expressions in the video. |
| negative_prompt | No | string | Negative prompt for video generation. Default: "". Used to steer the generation away from unwanted features. |
| seed | No | integer | Random seed for reproducibility. If not provided, a random seed is chosen. |
| resolution | No | string | Resolution of the generated video. Default: "480p". Available values: "480p", "580p", "720p". |
| num_inference_steps | No | integer | Number of inference steps for sampling. Higher values give better quality but take longer. Default: 27. |
| enable_safety_checker | No | boolean | If set to true, input data will be checked for safety before processing. |
| guidance_scale | No | float | Classifier-free guidance scale. Higher values give better adherence to the prompt but may decrease quality. Default: 3.5. |
| shift | No | float | Shift value for the video. Must be between 1.0 and 10.0. Default: 5. |
| video_quality | No | string | The quality of the output video. Higher quality means better visual quality but larger file size. Default: "high". Values: "low", "medium", "high", "maximum". |
| video_write_mode | No | string | The write mode of the output video. Default: "balanced". Values: "fast" (faster results, larger file), "balanced" (compromise), "small" (slowest, smallest file). |
**Example Request**
```
{
"prompt": "Summer beach vacation style, a man wearing sunglasses Blue Tshirt.",
"image_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/input_model.png",
"audio_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/input_music.mp3"
}
```
**Response**
```
{
"request_id": "wan-2-2-14b-speech-to-video_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/wan-2-2-14b-speech-to-video_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```
#### Wan 2.2 Animate API Documentation
https://gateway.pixazo.ai/wan-2-2-animate-api-524/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Generate Request - Wan 2.2 Animate
**Request Code**
```
POST /wan-2-2-animate-api-request HTTP/1.1
```
Host: gateway.pixazo.ai
```
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"video_url": "https://example.com/motion-source.mp4",
"image_url": "https://example.com/target-image.png"
}
```
**Output**
```
{
"request_id": "wan-2-2-animate-api-524_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/wan-2-2-animate-api-524_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Generate Request
| Field | Type | Required | Default | Description |
| video_url | string | Yes | — | URL pointing to a video file that will serve as the motion source. The API extracts motion patterns from this video to animate the target image. |
| image_url | string | Yes | — | URL pointing to a static image that will be animated using the motion from the video. Must be a valid, publicly accessible image (PNG, JPEG, etc.). |
Minimum Request
```
{
"video_url": "https://example.com/motion-source.mp4",
"image_url": "https://example.com/target-image.png"
}
```
Full Request (all options)
```
{
"video_url": "https://example.com/motion-source.mp4",
"image_url": "https://example.com/target-image.png"
}
```
**Response**
```
{
"request_id": "wan-2-2-animate-api-524_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/wan-2-2-animate-api-524_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	Your API subscription key
```
#### Wan 2.2 Image To Video(First Frame) API Documentation
https://gateway.pixazo.ai/wan-i2v/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Wan Image to Video First Frame - Wan Image to Video API
**Request Code**
```
POST https://gateway.pixazo.ai/wan-i2v/v1/generateImageToVideoRequest
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"model": "wan2.2-i2v-plus",
"input": {
"prompt": "Banana dancing in a traditional dress",
"negative_prompt": "flowers, blur",
"img_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/nano-banana.jpeg"
},
"parameters": {
"resolution": "1080P",
"duration": 5,
"prompt_extend": true,
"watermark": false,
"seed": 12345
}
}
```
**Output**
```
{
"request_id": "wan-image-to-video_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/wan-image-to-video_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Wan Image to Video First Frame
| Parameter | Required | Type | Description |
| model | Yes | string | Model to use. Available values: "wan2.2-i2v-flash", "wan2.2-i2v-plus" (recommended), "wan2.1-i2v-plus", "wan2.1-i2v-turbo". |
| input.img_url | Yes | string | URL of the first frame image. Must be publicly accessible HTTP/HTTPS URL. Supports JPEG, JPG, PNG, BMP, WEBP. Max size: 10MB. Image resolution: 360-2000 pixels. |
| input.prompt | No | string | Default: null. Text description to guide video generation. Supports English and Chinese, up to 800 characters. |
| input.negative_prompt | No | string | Default: null. Elements to exclude from the video. Up to 500 characters. |
| parameters.resolution | No | string | Default varies by model: |
- wan2.2-i2v-plus: "480P" or "1080P" (default: "1080P")
- wan2.2-i2v-flash: "480P" or "720P" (default: "720P")
- wan2.1-i2v-plus: only "720P"
- wan2.1-i2v-turbo: "480P" or "720P" (default: "720P")
| parameters.duration | No | integer | Default: 5. Video duration in seconds. For wan2.1-i2v-turbo: 3, 4, or 5. Other models fixed at 5. |
| parameters.prompt_extend | No | boolean | Default: true. When enabled, uses LLM to enhance the prompt. Improves quality but adds processing time. |
| parameters.watermark | No | boolean | Default: false. When true, adds "Generated by AI" watermark at bottom-right. |
| parameters.seed | No | integer | Default: null. Random seed for reproducible results. Range: 0-2147483647. |
**Example Request**
```
{
"model": "wan2.2-i2v-plus",
"input": {
"prompt": "Banana dancing in a traditional dress",
"negative_prompt": "flowers, blur",
"img_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/nano-banana.jpeg"
},
"parameters": {
"resolution": "1080p",
"duration": 5,
"prompt_extend": true,
"watermark": false,
"seed": 12345
}
}
```
**Response**
```
{
"request_id": "wan-image-to-video_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/wan-image-to-video_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```
#### Wan 2.2 Keyframe To Video API Documentation
https://gateway.pixazo.ai/wan-image-to-video/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Wan Keyframe to Video - Wan Image to Video API
**Request Code**
```
POST https://gateway.appypie.com/wan-i2v/v1/generateImageToVideoFrameRequest
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"model": "wan2.1-kf2v-plus",
"input": {
"first_frame_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/wan-t2i/wan-t2i-75d44f7d-a954-46b0-a603-10c09cb5df84-0.png",
"last_frame_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/wan-t2i/wan-t2i-47c06b16-ce7f-4977-8f7c-a04384409934-0.png",
"prompt": "Realistic style. Aeroplane from takeoff to fly captured in camera",
"negative_prompt": "person, text"
},
"parameters": {
"resolution": "720P",
"prompt_extend": true,
"watermark": false,
"seed": 12345
}
}
```
**Output**
```
{
"request_id": "wan-image-to-video_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/wan-image-to-video_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Wan Keyframe to Video
| Parameter | Required | Type | Description |
| model | Yes | string | Model to use. Available value: "wan2.1-kf2v-plus" (keyframe-to-video model). |
| input.first_frame_url | Yes | string | URL of the first frame image. Must be a publicly accessible HTTP/HTTPS URL. Supports JPEG, JPG, PNG, BMP, WEBP. Max size: 10MB. Resolution: 360–2000 pixels. |
| input.last_frame_url | Yes | string | URL of the last frame image. Must be a publicly accessible HTTP/HTTPS URL. Supports JPEG, JPG, PNG, BMP, WEBP. Max size: 10MB. Resolution: 360–2000 pixels. |
| input.prompt | No | string | Default: null. Text description to guide video transition between frames. Supports English and Chinese, up to 800 characters. Useful for camera/subject changes. |
| input.negative_prompt | No | string | Default: null. Elements to exclude from the video. Up to 500 characters. |
| parameters.resolution | No | string | Default: "720P". Currently only "720P" is supported. Typical resolution is 1280×720 with 16:9 aspect ratio. |
| parameters.duration | No | integer | Default: 5. Video duration in seconds. Fixed at 5 seconds and cannot be changed. |
| parameters.prompt_extend | No | boolean | Default: true. When enabled, uses LLM to enhance the prompt. Improves quality but adds processing time. |
| parameters.watermark | No | boolean | Default: false. When true, adds an "AI-generated" watermark at the bottom-right corner. |
| parameters.seed | No | integer | Default: null. Random seed for reproducible results. Range: 0–2147483647. |
**Example Request**
```
{
"model": "wan2.1-kf2v-plus",
"input": {
"first_frame_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/wan-t2i/wan-t2i-75d44f7d-a954-46b0-a603-10c09cb5df84-0.png",
"last_frame_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/wan-t2i/wan-t2i-47c06b16-ce7f-4977-8f7c-a04384409934-0.png",
"prompt": "Realistic style. Aeroplane from takeoff to fly captured in camera",
"negative_prompt": "person, text"
},
"parameters": {
"resolution": "720P",
"prompt_extend": true,
"watermark": false,
"seed": 12345
}
}
```
**Response**
```
{
"request_id": "wan-image-to-video_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/wan-image-to-video_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```
#### Wan 2.2 Edit Image API Documentation
https://gateway.pixazo.ai/wan-t2i/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Edit Image Request - Wan Text to Image API
**Request Code**
```
POST https://gateway.pixazo.ai/wan-t2i/v1/generateEditImageRequest
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"model": "wanx2.1-imageedit",
"input": {
"function": "stylization_all",
"prompt": "A dreamy watercolor style",
"base_image_url": "https://example.com/image.jpg"
},
"parameters": {
"n": 1
}
}
```
**Output**
```
{
"request_id": "wan-text-to-image_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/wan-text-to-image_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Edit Image Request
| Parameter | Required | Type | Description |
| model | Yes | string | Model to use. Available value: "wanx2.1-imageedit". |
| input.function | Yes | string | Function to apply. Example: "stylization_all". |
| input.prompt | Yes | string | Text prompt describing the desired edit. Supports English and Chinese. |
| input.base_image_url | Yes | string | URL of the base image to edit. |
| parameters.n | No | integer | Number of images to generate (default: 1). |
**Example Request**
```
{
"model": "wanx2.1-imageedit",
"input": {
"function": "stylization_all",
"prompt": "A dreamy watercolor style",
"base_image_url": "https://example.com/image.jpg"
},
"parameters": {
"n": 1
}
}
```
**Response**
```
{
"request_id": "wan-text-to-image_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/wan-text-to-image_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```
#### Wan 2.2 Text To Image API Documentation
https://gateway.pixazo.ai/wan-t2i/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Text To Image Request - Wan Text to Image API
**Request Code**
```
POST https://gateway.pixazo.ai/wan-t2i/v1/generateTextToImageRequest
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"model": "wan2.2-t2i-flash",
"input": {
"prompt": "A beautiful mountain landscape at sunset"
}
}
```
**Output**
```
{
"request_id": "wan-text-to-image_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/wan-text-to-image_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Text To Image Request
| Parameter | Required | Type | Description |
| model | Yes | string | Model to use. Available values: "wan2.2-t2i-flash", "wan2.2-t2i-plus", "wan2.1-t2i-turbo", "wan2.1-t2i-plus". |
| input.prompt | Yes | string | Positive prompt describing the image. Supports English and Chinese, up to 800 characters. |
| input.negative_prompt | No | string | Default: null. Elements to exclude from the image (up to 500 characters). |
| parameters.size | No | string | Default: "1024x1024". Resolution in widthxheight, range 512–1440, max 2M pixels. |
| parameters.n | No | integer | Default: 1. Number of images (1–4). |
| parameters.seed | No | integer | Default: null. Random seed for reproducible results (0–2147483647). |
| parameters.prompt_extend | No | boolean | Default: false. Enhances the prompt using LLM. Adds 3–4s processing time. |
| parameters.watermark | No | boolean | Default: false. Adds "AI Generated" watermark at bottom-right. |
**Example Request**
```
{
"model": "wan2.2-t2i-flash",
"input": {
"prompt": "A beautiful mountain landscape at sunset",
"negative_prompt": "people, buildings, text, watermark, signature"
},
"parameters": {
"size": "1024*1024",
"n": 1,
"seed": 42,
"prompt_extend": false,
"watermark": false
}
}
```
**Response**
```
{
"request_id": "wan-text-to-image_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/wan-text-to-image_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```
#### Wan 2.2 Text To Video API Documentation
https://gateway.pixazo.ai/wan-video/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Generate Text To Video Request - Wan Text to Video API
**Request Code**
```
POST https://gateway.pixazo.ai/wan-video/v1/generateTextToVideoRequest
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"model": "wan2.2-t2v-plus",
"input": {
"prompt": "A kitten running in the moonlight",
"negative_prompt": "flowers, people, text"
},
"parameters": {
"size": "1920*1080"
}
}
```
**Output**
```
{
"request_id": "wan-text-to-video_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/wan-text-to-video_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Generate Text To Video Request
| Parameter | Required | Type | Description |
| model | Yes | string | Model to use. Available values: "wan2.2-t2v-plus" (recommended), "wanx2.1-t2v-turbo", "wanx2.1-t2v-plus". |
| input.prompt | Yes | string | Text description of the video to generate. Supports English and Chinese. |
| input.negative_prompt | No | string | Default: null. Elements to exclude from the video. |
| parameters.size | No | string | Default: "1280*720". Available resolutions vary by model. |
| parameters.n | No | integer | Default: 1. Number of videos to generate. Currently only 1 is supported. |
**Example Request**
```
{
"model": "wan2.2-t2v-plus",
"input": {
"prompt": "A kitten running in the moonlight",
"negative_prompt": "flowers, people, text"
},
"parameters": {
"size": "1920*1080"
}
}
```
**Response**
```
{
"request_id": "wan-text-to-video_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/wan-text-to-video_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```

---

## Audio & Music

### ElevenLabs API - AI Voice & Music Generation APIs
**Page:** https://www.pixazo.ai/models/elevenlabs


by ElevenLabs

ElevenLabs API, developers can generate lifelike speech in multiple languages, clone voices from short audio samples, and create original music tracks. The API powers applications ranging from audiobook narration and podcast production to real-time voice assistants and multilingual content localization with human-quality audio output.

Models Version
ElevenLabs v3
ElevenLabs v1
V3 Alpha
V3 Alpha
**Request Code**
**Request Parameters**
**Example Request**
**Response**
**Request Headers**
**Response Handling**
**Pricing**
#### ElevenLabs v3 V3 Alpha API Documentation
https://gateway.pixazo.ai/eleven-v3-alpha-954/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Eleven v3 Alpha generate request - Eleven v3 Alpha
**Request Code**
```
POST https://gateway.pixazo.ai/eleven-v3-alpha-954/v1/eleven-v3-alpha-request
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"text": "Hello! This is a test of the text to speech system, powered by ElevenLabs. How does it sound?",
"voice": "Aria",
"stability": 0.5,
"similarity_boost": 0.75,
"speed": 1
}
```
**Output**
```
{
"request_id": "eleven-v3-alpha-954_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/eleven-v3-alpha-954_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Eleven v3 Alpha generate request
| Parameter | Required | Type | Description |
| text | Yes | string | The input text to be converted into speech. Must be non-empty and within the model's length limits. |
| voice | Yes | string | Name of the ElevenLabs voice preset to use for synthesis. Valid values include: "Aria", "Domi", "Bella", "Rachel", "Antoni", etc. |
| stability | Optional | number | Controls the voice's stability and consistency. Lower values increase variability; higher values produce more consistent, robotic tones. Range: 0.0–1.0. |
| similarity_boost | Optional | number | Enhances the voice’s similarity to the original training sample. Higher values yield more natural voice characteristics; lower values improve clarity and flexibility. Range: 0.0–1.0. |
| speed | Optional | number | Adjusts the playback speed of the generated audio. Values less than 1.0 slow down speech; values greater than 1.0 speed it up. Recommended range: 0.5–2.0. |
**Example Request**
```
{
"text": "Hello! This is a test of the text to speech system, powered by ElevenLabs. How does it sound?",
"voice": "Aria",
"stability": 0.5,
"similarity_boost": 0.75,
"speed": 1
}
```
**Response**
```
{
"request_id": "eleven-v3-alpha-954_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/eleven-v3-alpha-954_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```
#### 2. ElevenLabs v1
#### ElevenLabs v1 Music Generation API Documentation
https://gateway.pixazo.ai/elevenlabs-music-api-368/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
ElevenLabs Music API generate request - ElevenLabs Music API
**Request Code**
```
POST https://gateway.pixazo.ai/elevenlabs-music-api-368/v1/elevenlabs-music-api-request
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"prompt": "Mysterious original soundtrack, themes of jungle, rainforest, nature, woodwinds, busy rhythmic tribal percussion.",
"respect_sections_durations": true,
"output_format": "mp3_44100_128"
}
```
**Output**
```
{
"request_id": "elevenlabs-music-api-368_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/elevenlabs-music-api-368_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - ElevenLabs Music API generate request
| Parameter | Required | Type | Description |
| prompt | Yes | string | A detailed textual description of the desired music style, mood, instruments, and structure. Example: “Mysterious original soundtrack, themes of jungle, rainforest, nature, woodwinds, busy rhythmic tribal percussion.” |
| respect_sections_durations | No | boolean | If true, the system will preserve duration timing cues embedded in the prompt (e.g., “45-second loop”, “30-second intro”). If false, duration is determined automatically. |
| output_format | No | string | The audio file format and quality specification. Supported values: `mp3_22050_32, mp3_44100_32, mp3_44100_64, mp3_44100_96, mp3_44100_128, mp3_44100_192, pcm_8000, pcm_16000, pcm_22050, pcm_24000, pcm_44100, pcm_48000, ulaw_8000, alaw_8000, opus_48000_32, opus_48000_64, opus_48000_96, opus_48000_128, opus_48000_192`. |
**Example Request**
```
{
"prompt": "Mysterious original soundtrack, themes of jungle, rainforest, nature, woodwinds, busy rhythmic tribal percussion.",
"respect_sections_durations": true,
"output_format": "mp3_44100_128"
}
```
**Response**
```
{
"request_id": "elevenlabs-music-api-368_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/elevenlabs-music-api-368_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```

---

### Lyria 3 Pro API, Lyria 2 API - AI Music Generation
**Page:** https://www.pixazo.ai/models/lyria


by Google

Lyria 3 Pro API is Google's advanced AI music generation model, designed to create high-quality, expressive audio content. Powered by cutting-edge deep learning, Lyria enables developers to generate music compositions through a simple API call.

Models Version
Lyria 3 Pro
Lyria 3
Lyria 2
Music Generation
Music Generation
**Request Code**
**Request Parameters**
**Example Request**
**Response**
**Request Headers**
**Response Handling**
**Pricing**
#### Lyria 3 Pro Music Generation API Documentation
https://gateway.pixazo.ai/lyria-3-pro/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Music generate request - Lyria 3 Pro API
**Request Code**
```
POST https://gateway.pixazo.ai/lyria-3-pro/v1/lyria-3-pro/generate
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"prompt": "A calm acoustic folk song with gentle guitar and soft strings. Instrumental only."
}
```
**Output**
```
{
"request_id": "lyria-3-pro_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/lyria-3-pro_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Music generate request
| Parameter | Required | Type | Description |
| prompt | Yes | string | Text prompt describing the song to generate. Include details like genre, instruments, mood, tempo, lyrics, and song structure (e.g. [Verse], [Chorus], [Bridge]). Use timestamps like [0:00 - 0:30] to control timing. |
| images | No | array | Input images to inspire the music composition (up to 10 images). Array of URL strings. |
| webhook | No | string | Webhook URL for async notifications when generation completes. |
| webhook_events_filter | No | array | Filter for webhook event types to receive (e.g. ["completed"]). |
**Example Request**
```
{
"prompt": "[Verse 1]\nWalking through the neon glow,\ncity lights reflect below,\nevery shadow tells a story,\nevery corner, fading glory.\n\n[Chorus]\nWe are the echoes in the night,\nburning brighter than the light,\nhold on tight, don't let me go,\nwe are the echoes down below.\n\n[Verse 2]\nFootsteps lost on empty streets,\nrhythms sync to heartbeats,\nwhispers carried by the breeze,\ndancing through the autumn leaves.\n\nGenre: Dreamy indie pop. Mood: Nostalgic and uplifting. Tempo: 110 BPM.",
"images": [
"https://example.com/sunset-beach.jpg",
"https://example.com/forest-path.jpg"
]
}
```
**Response**
```
{
"request_id": "lyria-3-pro_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/lyria-3-pro_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```
#### 2. Lyria 3

Documentation coming soon!

apiId: lyria-3 and operation: music-request

#### 3. Lyria 2
#### Lyria 2 Music Generation API Documentation
https://gateway.pixazo.ai/lyria-2/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Music Request - Lyria 2
**Request Code**
```
POST https://gateway.pixazo.ai/lyria-2/v1/lyria-2/generate
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_API_KEY
{
"prompt": "Futuristic country music, steel guitar, huge 808s"
}
```
**Output**
```
{
"request_id": "lyria-2_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/lyria-2_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Music Request
| Parameter | Required | Type | Description |
| prompt | Yes | string | Text prompt describing the music to generate |
| negative_prompt | No | string | Description of what to exclude from the generated audio |
| seed | No | integer | Random seed for reproducible generation |
| webhook | No | string | Webhook URL for async notifications when generation completes |
| webhook_events_filter | No | array | Event types to receive (e.g. ["completed"]) |
**Example Request**
```
{
"prompt": "Futuristic country music, steel guitar, huge 808s, synth wave elements space western cosmic twang soaring vocals",
"negative_prompt": "low quality, distorted, noise, static, vocals out of tune",
"seed": 42,
"webhook": "https://your-webhook.com/callback",
"webhook_events_filter": ["completed"]
}
```
**Response**
```
{
"request_id": "lyria-2_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/lyria-2_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	Your API subscription key
```
Music Status - Lyria 2
**Request Code**
```
POST https://gateway.pixazo.ai/lyria-2/v1/lyria-2/prediction
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_API_KEY
{
"prediction_id": "abc123def456"
}
```
**Output**
```
{
"success": true,
"request_id": "abc123def456",
"status": "succeeded",
"audio": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/lyria-2/abc123def456_output_0.wav",
"created_at": "2026-02-25T10:00:00.000Z"
}
```
Request Parameters - Music Status
| Parameter | Required | Type | Description |
| prediction_id | Yes | string | The prediction ID returned from the generate endpoint |
**Example Request**
```
{
"prediction_id": "abc123def456"
}
```
**Response**
```
{
"audio": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/lyria-2/abc123def456_output_0.wav",
"success": true,
"request_id": "abc123def456",
"status": "succeeded",
"input": {
"prompt": "Futuristic country music, steel guitar, huge 808s"
},
"created_at": "2026-02-25T10:00:00.000Z"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	Your API subscription key
```

---

## Virtual Try-On

### Fashn Virtual Try On API - AI Virtual Try-On APIs
**Page:** https://www.pixazo.ai/models/fashn-virtual-try-on


by Fashn

Fashn Virtual Try On API, e-commerce platforms and fashion retailers can enable customers to visualize how garments will look on them before purchasing. The API handles complex fabric physics, body positioning, and lighting to create convincing virtual fitting experiences that reduce returns and increase conversion.

Models Version
Fashn v1.6
Glass v1
Virtual Try-On
Virtual Try-On
**Request Code**
**Request Parameters**
**Example Request**
**Response**
**Request Headers**
**Response Handling**
**Pricing**
#### Fashn v1.6 Virtual Try-On API Documentation
https://gateway.pixazo.ai/fashn-virtual-try-on/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Fashn Virtual Try-On generate request - Fashn Virtual Try-On API
**Request Code**
```
POST https://gateway.pixazo.ai/fashn-virtual-try-on/v1/fashn-virtual-try-on-request
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"model_image": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/vt_human.jpg",
"garment_image": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/vt_top.jpeg",
"category": "auto",
"mode": "balanced",
"garment_photo_type": "auto",
"moderation_level": "permissive",
"num_samples": 1,
"segmentation_free": true,
"output_format": "png"
}
```
**Output**
```
{
"request_id": "fashn-virtual-try-on_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/fashn-virtual-try-on_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Fashn Virtual Try-On generate request
| Parameter | Required | Type | Description |
| model_image | Yes | string | URL to a human model image (full body or upper body). Must be accessible via public HTTP/HTTPS endpoint. |
| garment_image | Yes | string | URL to a garment image (on-model or flat-lay). Must be a public HTTP/HTTPS endpoint. |
| category | No | string | Specifies garment category to improve segmentation. Values: auto, top, bottom, dress, outerwear. |
| mode | No | string | Processing mode balancing speed and quality. Values: fast, balanced, high_quality. |
| garment_photo_type | No | string | Specifies the type of garment image provided. Values: auto, on_model, flat_lay. |
| moderation_level | No | string | Content moderation sensitivity. Values: strict, moderate, permissive. |
| num_samples | No | integer | Number of try-on variations to generate. Maximum value: 5. |
| segmentation_free | No | boolean | When true, bypasses detailed segmentation for faster processing (may reduce accuracy on complex garments). |
| output_format | No | string | Output image format. Values: png, jpeg. |
**Example Request**
```
{
"model_image": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/vt_human.jpg",
"garment_image": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/vt_top.jpeg",
"category": "auto",
"mode": "balanced",
"garment_photo_type": "auto",
"moderation_level": "permissive",
"num_samples": 1,
"segmentation_free": true,
"output_format": "png"
}
```
**Response**
```
{
"request_id": "fashn-virtual-try-on_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/fashn-virtual-try-on_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```
#### 2. Glass v1
#### Glass v1 Text To Image API Documentation
**Request Code**
```
POST https://gateway.pixazo.ai/glass-virtual-try-on/v1/api/glass-virtual-tryon
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"model_image_path": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/model.png",
"glass_image_path": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/Capturade-tela-2025-07-17%20171034.png"
}
```
**Output**
```
{
"success": true,
"request_id": "abc-123-xyz",
"status": "submitted"
}
```
Request Parameters - Generate
| Parameter | Required | Type | Description |
| model_image_path | Yes | string | Support inputting image Base64 encoding or image URL (ensure accessibility). Ex: https://pub-582b7213209642b9b995c96c95a30381.r2.dev/model.png. Please note, if you use the Base64 method, make sure all image data parameters you pass are in Base64 encoding format. When submitting data, do not add any prefixes to the Base64-encoded string, such as data:image/png;base64. The correct parameter format should be the Base64-encoded string itself. Supported image formats include .jpg / .jpeg / .png. The image file size cannot exceed 10MB, and the image resolution should not be less than 300*300px |
| glass_image_path | No | string | Default: null. Support inputting image Base64 encoding or image URL (ensure accessibility). Ex: https://pub-582b7213209642b9b995c96c95a30381.r2.dev/Capturade-tela-2025-07-17%20171034.png. Please note, if you use the Base64 method, make sure all image data parameters you pass are in Base64 encoding format. When submitting data, do not add any prefixes to the Base64-encoded string, such as data:image/png;base64. The correct parameter format should be the Base64-encoded string itself. Supported image formats include .jpg / .jpeg / .png. The image file size cannot exceed 10MB, and the image resolution should not be less than 300*300px |
| num_images | No | number | Default: 1. Number of images to generate |
| output_format | No | string | Default: "jpeg". Output format for the images. Possible values: "jpeg", "png" |
**Example Request**
```
{
"model_image_path": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/model.png",
"glass_image_path": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/Capturade-tela-2025-07-17%20171034.png"
}
```
**Response**
```
{
"success": true,
"request_id": "abc-123-xyz",
"status": "submitted",
"message": "Request submitted"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```
#### Glass v1 Virtual Try-On API Documentation
**Request Code**
```
POST https://gateway.pixazo.ai/glass-virtual-try-on/v1/api/glass-virtual-tryon
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"model_image_path": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/model.png",
"glass_image_path": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/Capturade-tela-2025-07-17%20171034.png",
"num_images": 1,
"output_format": "jpeg"
}
```
**Output**
```
{
"success": true,
"request_id": "abc-123-xyz",
"status": "submitted",
"message": "Request submitted"
}
```
Request Parameters - Glass Virtual Try On
| Parameter | Required | Type | Description |
| model_image_path | Yes | string | Support inputting image Base64 encoding or image URL (ensure accessibility). Ex: https://pub-582b7213209642b9b995c96c95a30381.r2.dev/model.png. Please note, if you use the Base64 method, make sure all image data parameters you pass are in Base64 encoding format. When submitting data, do not add any prefixes to the Base64-encoded string, such as data:image/png;base64. The correct parameter format should be the Base64-encoded string itself. Supported image formats include .jpg / .jpeg / .png. The image file size cannot exceed 10MB, and the image resolution should not be less than 300*300px |
| glass_image_path | No | string | Default: null. Support inputting image Base64 encoding or image URL (ensure accessibility). Ex: https://pub-582b7213209642b9b995c96c95a30381.r2.dev/Capturade-tela-2025-07-17%20171034.png. Please note, if you use the Base64 method, make sure all image data parameters you pass are in Base64 encoding format. When submitting data, do not add any prefixes to the Base64-encoded string, such as data:image/png;base64. The correct parameter format should be the Base64-encoded string itself. Supported image formats include .jpg / .jpeg / .png. The image file size cannot exceed 10MB, and the image resolution should not be less than 300*300px |
| num_images | No | number | Default: 1. Number of images to generate |
| output_format | No | string | Default: "jpeg". Output format for the images. Possible values: "jpeg", "png" |
**Example Request**
```
{
"model_image_path": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/model.png",
"glass_image_path": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/Capturade-tela-2025-07-17%20171034.png",
"num_images": 1,
"output_format": "jpeg"
}
```
**Response**
```
{
"success": true,
"request_id": "abc-123-xyz",
"status": "submitted",
"message": "Request submitted"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```

---


## Additional Models

### Recraft V4 Pro API, Recraft V3 API - AI Image Generation APIs
**Page:** https://www.pixazo.ai/models/recraft


by Recraft

Recraft V4 Pro API. The API is designed for design professionals, marketers, and creative teams requiring production-quality image generation with reliable, repeatable outputs.

Models Version
Recraft V4 Pro
Recraft V4 Normal
Recraft v3
Text To Image
Text To Image
**Request Code**
**Request Parameters**
**Example Request**
**Response**
**Request Headers**
**Response Handling**
**Pricing**
#### Recraft V4 Pro Text To Image API Documentation
https://gateway.pixazo.ai/recraft
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Text to Image V4 - Pro - Recraft
**Request Code**
```
POST https://gateway.pixazo.ai/recraft/v4-pro/generate
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: your-subscription-key
{
"prompt": "a red cat"
}
```
**Output**
// When n=1 (default), output is a string:
```
{
"output": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/recraft/a-red-cat-1773760417132-0.webp"
}
// When n>1, output is an array:
{
"output": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/recraft/a-red-cat-1773760417132-0.webp",
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/recraft/a-red-cat-1773760417132-1.webp"
]
}
```
Request Parameters - Text to Image V4 - Pro
| Field | Type | Required | Default | Description |
| prompt | string | Yes | — | The text query that instructs the AI model on what kind of image to generate. |
| size | string | No | 2048x2048 | Image dimensions or aspect ratio. See documentation for supported values. |
| n | integer | No | 1 | Number of images to generate. Minimum: 1, Maximum: 6. |
| controls | object | No | — | Additional generation controls. |
Minimum Request
```
{
"prompt": "a red cat"
}
```
Full Request (all options)
```
{
"prompt": "A detailed architectural rendering of a modern glass building surrounded by lush gardens, golden hour lighting, ultra detailed, professional photography",
"size": "2560x1664",
"n": 6,
"controls": {}
}
```
**Response**
// Single image (n=1, default):
```
{
"output": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/recraft/generated-image-0.webp"
}
// Multiple images (n>1):
{
"output": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/recraft/generated-image-0.webp",
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/recraft/generated-image-1.webp"
]
}
```
Response Fields - Text to Image V4 - Pro
| Field | Type | Description |
| output | string or array | URL(s) to the generated image(s). Returns a string when n=1, or an array of strings when n>1. Each URL points to a .webp image hosted on Cloudflare R2. |
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	Your API subscription key
```
**Response Handling**

Common status codes for Text to Image V4 - Pro.

| Code | Meaning |
| 200 | Success |
| 400 | Bad Request |
| 401 | Unauthorized |
| 403 | Forbidden |
| 404 | Not Found |
| 429 | Too Many Requests |
| 500 | Internal Server Error |
**Error Responses**
400 Bad Request
```
{
"error": "Missing required field: prompt",
"status": 400
}
```
502 Bad Gateway
```
{
"error": "Failed to reach upstream API",
"status": 502
}
```
Notes

Recraft V4 Pro does not support the style, style_id, or negative_prompt parameters. Passing any of these parameters will result in a 400 Bad Request error — they are not silently ignored. Use the exact prompt text for best results. Image generation typically takes 30–50 seconds for single images. Multiple images or higher resolutions may take longer. The output field returns a string URL when n=1, and an array of string URLs when n>1. Generated images are in .webp format. Supported image dimensions include: 2048x2048, 3072x1536, 1536x3072, 2560x1664, 1664x2560, 2432x1792, 1792x2432, 2304x1792, 1792x2304, 1664x2688, 2560x1792, 1792x2560, 2688x1536, 1536x2688, and aspect ratios: 1:1, 2:1, 1:2, 3:2, 2:3, 4:3, 3:4, 5:4, 4:5, 6:10, 14:10, 10:14, 16:9, 9:16.

**Recraft V4 Pro Text To Image API Pricing**
10% OFF
Limited time discount on API pricing
| Resolution | Price (USD) |
| All Resolution | $0.25 |
| All Resolution | $0.04 |
#### 2. Recraft V4 Normal
#### Recraft V4 Normal Text To Image API Documentation
https://gateway.pixazo.ai/recraft
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Text to Image V4 - Normal - Recraft
**Request Code**
```
POST https://gateway.pixazo.ai/recraft/v4/generate
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: <your-subscription-key>
{
"prompt": "a red cat"
}
```
**Output**
// When n=1 (default), output is a string:
```
{
"output": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/recraft/a-red-cat-1773760447344-0.webp"
}
// When n>1, output is an array:
{
"output": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/recraft/a-red-cat-1773760447344-0.webp",
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/recraft/a-red-cat-1773760447344-1.webp"
]
}
```
Request Parameters - Text to Image V4 - Normal
| Field | Type | Required | Default | Description |
| prompt | string | Yes | — | The text query that instructs the AI model on what kind of image to generate. |
| size | string | No | 1024x1024 | Image dimensions or aspect ratio. See Sizes section below. |
| n | integer | No | 1 | Number of images to generate. Default: 1 (minimum: 1, maximum: 6). |
| controls | object | No | — | Additional generation controls. |
Minimum Request
```
{
"prompt": "a red cat"
}
```
Full Request (all options)
```
{
"prompt": "Picture a sleek, futuristic car racing through a neon-lit cityscape, its engine humming efficiently as it blurs past digital billboards. The driver skillfully navigates the glowing streets, aiming for victory in this high-tech, adrenaline-fueled race of tomorrow.",
"size": "1536x768",
"n": 6,
}
```
**Response**
// Single image (n=1, default):
```
{
"output": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/recraft/generated-image-0.webp"
}
// Multiple images (n>1):
{
"output": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/recraft/generated-image-0.webp",
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/recraft/generated-image-1.webp",
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/recraft/generated-image-2.webp"
]
}
```
Response Fields - Text to Image V4 - Normal
| Field | Type | Description |
| output | string or array | URL(s) to the generated image(s). Returns a string for single image, array for multiple images. |
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	Your API subscription key
```
**Response Handling**

Common status codes for Text to Image V4 - Normal.

| Code | Meaning |
| 200 | Success |
| 400 | Bad Request |
| 401 | Unauthorized |
| 403 | Forbidden |
| 404 | Not Found |
| 429 | Too Many Requests |
| 500 | Internal Server Error |
**Error Responses**
400 Bad Request
```
{
"error": "Missing required field: prompt",
"status": 400
}
```
502 Bad Gateway
```
{
"error": "Failed to reach upstream API",
"status": 502
}
```
Notes

Recraft V4 does not support the style, style_id, or negative_prompt parameters. Passing any of these parameters will result in a 400 Bad Request error — they are not silently ignored.

Image generation typically completes in 15–25 seconds. For high-resolution outputs or multiple images, processing may take slightly longer.

The output field returns a string URL when n=1, and an array of string URLs when n>1. Generated images are in .webp format.

**Recraft V4 Normal Text To Image API Pricing**
10% OFF
Limited time discount on API pricing
| Resolution | Price (USD) |
| All Resolution | $0.25 |
| All Resolution | $0.04 |
#### 3. Recraft v3
#### Recraft v3 Image to Image API Documentation
https://gateway.pixazo.ai/recraft
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Image to Image V3 - Recraft
**Request Code**
```
POST https://gateway.pixazo.ai/recraft/v3/image-to-image
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key:
{
"image": "https://example.com/source.png",
"prompt": "winter landscape",
"strength": 0.5
}
```
**Output**
```
{
"output": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/recraft/winter-landscape-1773764135991-0.webp"
}
```
Request Parameters - Image to Image V3
| Field | Type | Required | Default | Description |
| image | string (URL) | Yes | — | URL of the source image (PNG/JPG/WEBP). Max 5MB, max 16MP, max 4096px on any side. |
| prompt | string | Yes | — | The text query that instructs the AI model on what kind of transformation to apply. |
| strength | decimal | Yes | — | Transformation strength. 0 = no change, 1 = full transformation. (minimum: 0, maximum: 1). |
| style | string | No | Recraft V3 Raw | Style preset name. See Styles section below. |
| style_id | string | No | — | UUID of a custom style created via the Recraft platform. Mutually exclusive with style. |
| n | integer | No | 1 | Number of images to generate. Default: 1 (minimum: 1, maximum: 6). |
| negative_prompt | string | No | — | Text describing what to avoid in the generated image. |
Minimum Request
```
{
"image": "https://example.com/source.png",
"prompt": "winter landscape",
"strength": 0.5
}
```
Full Request (all options)
```
{
"image": "https://example.com/source.png",
"prompt": "winter landscape with snow-covered mountains, frozen lake, northern lights in the sky",
"strength": 0.85,
"style": "Photorealism",
"n": 4,
"negative_prompt": "dark, blurry, low quality"
}
```
**Response**
When n=1 (default) — output is a string:
```
{
"output": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/recraft/winter-landscape-1773764135991-0.webp"
}
```
When n>1 (e.g., n=4) — output is an array:
```
{
"output": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/recraft/winter-landscape-1773764135991-0.webp",
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/recraft/winter-landscape-1773764135991-1.webp",
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/recraft/winter-landscape-1773764135991-2.webp",
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/recraft/winter-landscape-1773764135991-3.webp"
]
}
```
Response Fields - Image to Image V3
| Field | Type | Description |
| output | string or array of strings | URL(s) of the generated image(s). Returns a single string if n=1, or an array of strings if n>1. |
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	Your API subscription key
```
**Response Handling**

Common status codes for Image to Image V3.

| Code | Meaning |
| 200 | Success |
| 400 | Bad Request |
| 401 | Unauthorized |
| 403 | Forbidden |
| 404 | Not Found |
| 429 | Too Many Requests |
| 500 | Internal Server Error |
Common Error Responses
400 Bad Request
```
{
"error": "Missing required field: prompt",
"status": 400
}
```
502 Bad Gateway
```
{
"error": "Failed to reach upstream API",
"status": 502
}
```
Notes & Tips

Usage guidelines and important details for Recraft V3 Image-to-Image API.

Image must be under 5MB and 16MP with max 4096px on any side.
Response time typically 15–25 seconds depending on complexity and load.
Style presets are case-sensitive. Use exact names from the Style list in the documentation.
Up to 6 images can be generated per request using the n parameter.
When n=1 (default), the output field is a plain string URL. When n>1, the output field is an array of string URLs. Handle both types in your client code.
Generated images are returned in .webp format and hosted on Cloudflare R2 storage.
**Recraft v3 Image to Image API Pricing**
10% OFF
Limited time discount on API pricing
| Resolution | Price (USD) |
| All Resolution | $0.25 |
| All Resolution | $0.04 |
#### Recraft v3 Text To Image API Documentation
https://gateway.pixazo.ai/recraft
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Text to Image V3 - Recraft
**Request Code**
```
POST https://gateway.pixazo.ai/recraft/v3/generate
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: <your-subscription-key>
{
"prompt": "a red cat"
}
```
**Output**
// When n=1 (default), output is a string:
```
{
"output": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/recraft/a-red-cat-1773760470488-0.webp"
}
// When n>1, output is an array:
{
"output": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/recraft/a-red-cat-1773760470488-0.webp",
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/recraft/a-red-cat-1773760470488-1.webp"
]
}
```
Request Parameters - Text to Image V3
| Field | Type | Required | Default | Description |
| prompt | string | Yes | — | The text query that instructs the AI model on what kind of image to generate. |
| style | string | No | Recraft V3 Raw | Style preset name. See Styles section below. |
| style_id | string | No | — | UUID of a custom style created via the Recraft platform. Mutually exclusive with style. |
| size | string | No | 1024x1024 | Image dimensions. Default: 1024x1024. See Sizes section below. |
| n | integer | No | 1 | Number of images to generate. Default: 1 (minimum: 1, maximum: 6). |
| negative_prompt | string | No | — | Text describing what to avoid in the generated image. |
| controls | object | No | — | Additional generation controls. |
Minimum Request
```
{
"prompt": "a red cat"
}
```
Full Request (all options)
```
{
"prompt": "red point siamese cat sitting on a windowsill, natural light, shot on Canon EOS R5",
"style": "Photorealism",
"size": "1280x1024",
"n": 4,
"negative_prompt": "dark, blurry, low quality"
}
```
**Response**
// Single image (n=1, default):
```
{
"output": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/recraft/generated-image-0.webp"
}
// Multiple images (n>1):
{
"output": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/recraft/generated-image-0.webp",
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/recraft/generated-image-1.webp",
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/recraft/generated-image-2.webp",
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/recraft/generated-image-3.webp"
]
}
```
Response Fields - Text to Image V3
| Field | Type | Description |
| output | array/string | Array of image URLs if n>1, or single URL string if n=1. Each URL points to a generated image. |
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	Your API subscription key
```
**Response Handling**

Common status codes for Text to Image V3.

| Code | Meaning |
| 200 | Success |
| 400 | Bad Request |
| 401 | Unauthorized |
| 403 | Forbidden |
| 404 | Not Found |
| 429 | Too Many Requests |
| 500 | Internal Server Error |
Error Responses - Text to Image V3
400 Bad Request
```
{
"error": "Missing required field: prompt",
"status": 400
}
```
502 Bad Gateway
```
{
"error": "Failed to reach upstream API",
"status": 502
}
```
Notes & Tips - Text to Image V3

Usage considerations for Recraft V3.

Generated images are rendered at 1MP resolution regardless of specified size.
Style names are case-sensitive and must match exactly from the supported presets.
Processing time is typically 10–15 seconds for single images. Multiple images or complex prompts may take slightly longer.
Multiple images (n>1) may be generated asynchronously with slight time delays between outputs.
The output field returns a string URL when n=1, and an array of string URLs when n>1.
Generated images are in .webp format and hosted on Cloudflare R2.
**Recraft v3 Text To Image API Pricing**
10% OFF
Limited time discount on API pricing
| Resolution | Price (USD) |
| All Resolution | $0.25 |
| All Resolution | $0.04 |

---

### Nano Banana 2 API, Nano Banana Pro API, Nano Banana API - AI Image Generation APIs
**Page:** https://www.pixazo.ai/models/nano-banana


by Google

Nano Banana 2 API, developers can access both standard and pro variants for text-to-image generation and image-to-image editing. The API combines Google's advanced AI research with practical image generation features, offering reliable quality for applications ranging from creative tools to automated content production.

Models Version
Nano Banana 2
Nano Banana Pro
Nano Banana Standard
Edit Image
Edit Image
**Request Code**
**Request Parameters**
**Example Request**
**Response**
**Request Headers**
**Response Handling**
**Pricing**
#### Nano Banana 2 Edit Image API Documentation
https://gateway.pixazo.ai/nano-banana-2/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Image Request - Nano Banana 2
**Request Code**
```
POST https://gateway.pixazo.ai/nano-banana-2/v1/nano-banana-2/generate
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_API_KEY
{
"prompt": "Photorealistic DSLR portrait of a man wearing a black cap, standing in a lush green park with trees and soft sunlight, natural lighting, realistic skin texture, shallow depth of field, ultra-detailed, no stylization.",
"aspect_ratio": "1:1"
}
```
**Output**
```
{
"request_id": "nano-banana-2_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/nano-banana-2_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Image Request
| Parameter | Required | Type | Description |
| prompt | Yes | string | Text description of the image to generate |
| image_input | No | array of URIs | Input images to transform or use as reference (up to 14 images) |
| aspect_ratio | No | string | Aspect ratio: "match_input_image", "1:1", "1:4", "1:8", "2:3", "3:2", "3:4", "4:1", "4:3", "4:5", "5:4", "8:1", "9:16", "16:9", "21:9" |
| resolution | No | string | Resolution: "1K", "2K", "4K". Higher resolutions take longer |
| google_search | No | boolean | Use Google Web Search grounding for real-time information |
| image_search | No | boolean | Use Google Image Search grounding for visual context |
| output_format | No | string | Output format: "jpg", "png" |
| webhook | No | string | Webhook URL for async notifications when generation completes |
| webhook_events_filter | No | array | Event types to receive (e.g. ["completed"]) |
**Example Request**
```
{
"prompt": "APhotorealistic DSLR portrait of a man wearing a black cap, standing in a lush green park with trees and soft sunlight, natural lighting, realistic skin texture, shallow depth of field, ultra-detailed, no stylization.",
"image_input": ["https://example.com/reference-image.jpg"],
"aspect_ratio": "1:1",
"resolution": "4K",
"google_search": false,
"image_search": false,
"output_format": "png",
"webhook": "https://your-webhook.com/callback",
"webhook_events_filter": ["completed"]
}
```
**Response**
```
{
"request_id": "nano-banana-2_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/nano-banana-2_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_API_KEY
```
Image Status - Nano Banana 2
**Request Code**
```
POST https://gateway.pixazo.ai/nano-banana-2/v1/nano-banana-2/prediction
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_API_KEY
{
"prediction_id": "abc123def456"
}
```
**Output**
```
{
"image": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/nano-banana-2/XXXXXXXXXXXXXXXXXXXX.jpg",
"success": true,
"request_id": "a5qrhzedyxrmw0cwkr2vg774y0",
"status": "succeeded",
"input": {
"aspect_ratio": "1:1",
"prompt": "Photorealistic DSLR portrait of a man wearing a black cap, standing in a lush green park with trees and soft sunlight, natural lighting, realistic skin texture, shallow depth of field, ultra-detailed, no stylization"
},
"created_at": "2026-02-27T07:35:06.487Z"
}
```
Request Parameters - Image Status
| Parameter | Required | Type | Description |
| prediction_id | Yes | string | The prediction ID returned from the generate endpoint |
**Example Request**
```
{
"prediction_id": "abc123def456"
}
```
**Response**
```
{
"image": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/nano-banana-2/XXXXXXXXXXXXXXXXXXXX.jpg",
"success": true,
"request_id": "a5qrhzedyxrmw0cwkr2vg774y0",
"status": "succeeded",
"input": {
"aspect_ratio": "1:1",
"prompt": "Photorealistic DSLR portrait of a man wearing a black cap, standing in a lush green park with trees and soft sunlight, natural lighting, realistic skin texture, shallow depth of field, ultra-detailed, no stylization"
},
"created_at": "2026-02-27T07:35:06.487Z"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_API_KEY
```
**Response Handling**

Common status codes for Image Status.

| Code | Meaning |
| 200 | Success |
| 400 | Bad Request |
| 401 | Unauthorized |
| 403 | Forbidden |
| 404 | Not Found |
| 429 | Too Many Requests |
| 500 | Internal Server Error |
**Response Handling**

Common status codes.

| Code | Meaning |
| 202 | Accepted — Request queued |
| 400 | Bad Request |
| 401 | Unauthorized |
| 402 | Insufficient Balance |
| 403 | Forbidden |
| 429 | Too Many Requests |
| 500 | Internal Server Error |
**Error Responses**

Queue system errors and model validation errors.

Queue System Errors
// 402 — Insufficient balance
```
{
"error": "Insufficient Balance",
"message": "Your wallet does not have enough balance."
}
// 400 — Model not found
{
"error": "Model not found",
"message": "Model 'nano-banana-2' not found or is disabled"
}
```
Error via Status/Webhook
```
{
"request_id": "nano-banana-2_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "ERROR",
"model_id": "nano-banana-2",
"error": "Description of the error",
"output": null
}
```
Retrieving Results

Poll the universal status endpoint to check progress and retrieve results.

Endpoint
```
GET https://gateway.pixazo.ai/v2/requests/status/{request_id}
Ocp-Apim-Subscription-Key: YOUR_API_KEY
```
cURL Example
```
curl -H "Ocp-Apim-Subscription-Key: YOUR_API_KEY" \
"https://gateway.pixazo.ai/v2/requests/status/nano-banana-2_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
```
Response (Completed)
```
{
"request_id": "nano-banana-2_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "COMPLETED",
"model_id": "nano-banana-2",
"error": null,
"output": {
"media_url": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/v1/nano-banana-2_019dxxxx-xxxx/output.ext"
],
"media_type": "application/octet-stream"
},
"created_at": "2026-03-31T10:00:00.000Z",
"updated_at": "2026-03-31T10:00:15.000Z",
"completed_at": "2026-03-31T10:00:15.000Z"
}
```
Response Fields
| Field | Type | Description |
| request_id | string | Unique request identifier |
| status | string | QUEUED, PROCESSING, COMPLETED, FAILED, or ERROR |
| model_id | string | Model that processed the request |
| error | string|null | Error message if failed |
| output.media_url | array | URLs to generated media (R2 CDN) |
| output.media_type | string | MIME type of the output |
| created_at | string | When request was created |
| completed_at | string|null | When request completed |
| polling_url | string | Status URL (initial response only) |
Status Values
| Status | Description |
| QUEUED | Request accepted, waiting to be processed |
| PROCESSING | Being processed by the model |
| COMPLETED | Done — output contains the result |
| FAILED | Failed — check error field |
| ERROR | System error — not charged |
Status Flow
QUEUED → PROCESSING → COMPLETED
→ FAILED
→ ERROR
Typical Workflow
Send a generate request to the API endpoint
Save the request_id from the response
Poll every 5-10 seconds: GET /v2/requests/status/{request_id}
When status is "COMPLETED", download from output.media_url

Tip: Use X-Webhook-URL header to get a callback instead of polling.

**Nano Banana 2 Edit Image API Pricing**
| Resolution | Price (USD) |
| 1K | $0.067 |
| 2K | $0.101 |
| 4K | $0.151 |
#### 2. Nano Banana Pro
#### Nano Banana Pro Generate Image API Documentation
https://gateway.pixazo.ai/nano-banana-pro-770/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Generate Request - Nano Banana Pro API
**Request Code**
```
POST https://gateway.pixazo.ai/nano-banana-pro-770/v1/nano-banana-pro-request
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"prompt": "make a photo of the man driving the car down the california coastline",
"image_urls": [
"https://storage.googleapis.com/falserverless/example_inputs/nano-banana-edit-input.png",
"https://storage.googleapis.com/falserverless/example_inputs/nano-banana-edit-input-2.png"
]
}
```
**Output**
```
{
"request_id": "nano-banana-pro-770_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/nano-banana-pro-770_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Generate Request
| Parameter | Required | Type | Description |
| prompt | Yes | string | The prompt describing the desired image edit or transformation. Clearly specify what should be changed, added, or preserved in the input images. |
| image_urls | Yes | array<string> | List of HTTPS URLs of images used for image-to-image generation or editing. Images must be publicly accessible. |
| num_images | No | integer | Number of images to generate. |
| aspect_ratio | No | string | Aspect ratio of the generated image. Possible values: auto, 21:9, 16:9, 3:2, 4:3, 5:4, 1:1, 4:5, 3:4, 2:3, 9:16. |
| output_format | No | string | Output image format. Possible values: jpeg, png, webp. |
| resolution | No | string | Resolution of the generated image. Possible values: 1K, 2K, 4K. |
| sync_mode | No | boolean | If true, the generated image is returned as a data URI and not stored in request history. |
| limit_generations | No | boolean | Experimental parameter that limits the number of generations per prompt round to 1. When set to true, any instructions in the prompt requesting multiple images are ignored. |
| enable_web_search | No | boolean | Enables web search during image generation, allowing the model to use up-to-date information from the web. |
**Example Request**
```
{
"prompt": "make a photo of the man driving the car down the california coastline",
"image_urls": [
"https://storage.googleapis.com/falserverless/example_inputs/nano-banana-edit-input.png",
"https://storage.googleapis.com/falserverless/example_inputs/nano-banana-edit-input-2.png"
]
}
```
**Response**
```
{
"request_id": "nano-banana-pro-770_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/nano-banana-pro-770_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```
**Response Handling**

Common status codes.

| Code | Meaning |
| 202 | Accepted — Request queued |
| 400 | Bad Request |
| 401 | Unauthorized |
| 402 | Insufficient Balance |
| 403 | Forbidden |
| 429 | Too Many Requests |
| 500 | Internal Server Error |
**Error Responses**

Queue system errors and model validation errors.

Queue System Errors
// 402 — Insufficient balance
```
{
"error": "Insufficient Balance",
"message": "Your wallet does not have enough balance."
}
// 400 — Model not found
{
"error": "Model not found",
"message": "Model 'nano-banana-pro-770' not found or is disabled"
}
```
Error via Status/Webhook
```
{
"request_id": "nano-banana-pro-770_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "ERROR",
"model_id": "nano-banana-pro-770",
"error": "Description of the error",
"output": null
}
```
Retrieving Results

Poll the universal status endpoint to check progress and retrieve results.

Endpoint
```
GET https://gateway.pixazo.ai/v2/requests/status/{request_id}
Ocp-Apim-Subscription-Key: YOUR_API_KEY
```
cURL Example
```
curl -H "Ocp-Apim-Subscription-Key: YOUR_API_KEY" \
"https://gateway.pixazo.ai/v2/requests/status/nano-banana-pro-770_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
```
Response (Completed)
```
{
"request_id": "nano-banana-pro-770_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "COMPLETED",
"model_id": "nano-banana-pro-770",
"error": null,
"output": {
"media_url": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/v1/nano-banana-pro-770_019dxxxx-xxxx/output.ext"
],
"media_type": "application/octet-stream"
},
"created_at": "2026-03-31T10:00:00.000Z",
"updated_at": "2026-03-31T10:00:15.000Z",
"completed_at": "2026-03-31T10:00:15.000Z"
}
```
Response Fields
| Field | Type | Description |
| request_id | string | Unique request identifier |
| status | string | QUEUED, PROCESSING, COMPLETED, FAILED, or ERROR |
| model_id | string | Model that processed the request |
| error | string|null | Error message if failed |
| output.media_url | array | URLs to generated media (R2 CDN) |
| output.media_type | string | MIME type of the output |
| created_at | string | When request was created |
| completed_at | string|null | When request completed |
| polling_url | string | Status URL (initial response only) |
Status Values
| Status | Description |
| QUEUED | Request accepted, waiting to be processed |
| PROCESSING | Being processed by the model |
| COMPLETED | Done — output contains the result |
| FAILED | Failed — check error field |
| ERROR | System error — not charged |
Status Flow
QUEUED → PROCESSING → COMPLETED
→ FAILED
→ ERROR
Typical Workflow
Send a generate request to the API endpoint
Save the request_id from the response
Poll every 5-10 seconds: GET /v2/requests/status/{request_id}
When status is "COMPLETED", download from output.media_url

Tip: Use X-Webhook-URL header to get a callback instead of polling.

**Nano Banana Pro Generate Image API Pricing**
| Resolution | Price (USD) |
| 1K | $0.15 |
| 2K | $0.15 |
| 4K | $0.3 |
#### Nano Banana Pro Image To Image(Batch) API Documentation
https://gateway.pixazo.ai/nano-banana-pro-async/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Image to Image(Edit Image) - Nano Banana Pro Async API
**Request Code**
```
POST https://gateway.pixazo.ai/nano-banana-pro-async/v1/nano-banana-pro-image-to-image
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"prompt": "Convert tree flower to yellow saffron",
"image_urls": ["https://pub-582b7213209642b9b995c96c95a30381.r2.dev/f2.png"]
}
```
**Output**
```
{
"request_id": "nano-banana-pro-async-api_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/nano-banana-pro-async-api_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Image to Image(Edit Image)
| Parameter | Required | Type | Description |
| prompt | Yes | string | Text description of the desired transformation (required) |
| image_urls | Yes | string[] | Array of input image URLs (1-5 images required) |
| num_images | No | integer | Number of image variations to generate (1-10) |
| aspect_ratio | No | string | Output image aspect ratio (see options below) |
| resolution | No | string | Output image resolution: "1K", "2K", or "4K" |
| enable_web_search | No | boolean | Enable Google Search grounding for more accurate results |
| sync_mode | No | boolean | Return base64 data URIs instead of R2 URLs |
| webhook | No | string | Webhook URL for automatic completion notifications |
**Example Request**
```
{
"prompt": "Transform into cyberpunk style with neon lights and holographic effects",
"image_urls": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/f2.png"
],
"num_images": 3,
"aspect_ratio": "16:9",
"resolution": "1K",
"enable_web_search": true,
"sync_mode": false,
"webhook": "https://your-domain.com/api/webhook/gemini"
}
```
**Response**
```
{
"request_id": "nano-banana-pro-async-api_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/nano-banana-pro-async-api_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```
**Response Handling**

Common status codes.

| Code | Meaning |
| 202 | Accepted — Request queued |
| 400 | Bad Request |
| 401 | Unauthorized |
| 402 | Insufficient Balance |
| 403 | Forbidden |
| 429 | Too Many Requests |
| 500 | Internal Server Error |
**Error Responses**

Queue system errors and model validation errors.

Queue System Errors
// 402 — Insufficient balance
```
{
"error": "Insufficient Balance",
"message": "Your wallet does not have enough balance."
}
// 400 — Model not found
{
"error": "Model not found",
"message": "Model 'nano-banana-pro-async-api' not found or is disabled"
}
```
Error via Status/Webhook
```
{
"request_id": "nano-banana-pro-async-api_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "ERROR",
"model_id": "nano-banana-pro-async-api",
"error": "Description of the error",
"output": null
}
```
Retrieving Results

Poll the universal status endpoint to check progress and retrieve results.

Endpoint
```
GET https://gateway.pixazo.ai/v2/requests/status/{request_id}
Ocp-Apim-Subscription-Key: YOUR_API_KEY
```
cURL Example
```
curl -H "Ocp-Apim-Subscription-Key: YOUR_API_KEY" \
"https://gateway.pixazo.ai/v2/requests/status/nano-banana-pro-async-api_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
```
Response (Completed)
```
{
"request_id": "nano-banana-pro-async-api_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "COMPLETED",
"model_id": "nano-banana-pro-async-api",
"error": null,
"output": {
"media_url": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/v1/nano-banana-pro-async-api_019dxxxx-xxxx/output.ext"
],
"media_type": "application/octet-stream"
},
"created_at": "2026-03-31T10:00:00.000Z",
"updated_at": "2026-03-31T10:00:15.000Z",
"completed_at": "2026-03-31T10:00:15.000Z"
}
```
Response Fields
| Field | Type | Description |
| request_id | string | Unique request identifier |
| status | string | QUEUED, PROCESSING, COMPLETED, FAILED, or ERROR |
| model_id | string | Model that processed the request |
| error | string|null | Error message if failed |
| output.media_url | array | URLs to generated media (R2 CDN) |
| output.media_type | string | MIME type of the output |
| created_at | string | When request was created |
| completed_at | string|null | When request completed |
| polling_url | string | Status URL (initial response only) |
Status Values
| Status | Description |
| QUEUED | Request accepted, waiting to be processed |
| PROCESSING | Being processed by the model |
| COMPLETED | Done — output contains the result |
| FAILED | Failed — check error field |
| ERROR | System error — not charged |
Status Flow
QUEUED → PROCESSING → COMPLETED
→ FAILED
→ ERROR
Typical Workflow
Send a generate request to the API endpoint
Save the request_id from the response
Poll every 5-10 seconds: GET /v2/requests/status/{request_id}
When status is "COMPLETED", download from output.media_url

Tip: Use X-Webhook-URL header to get a callback instead of polling.

**Nano Banana Pro Image To Image(Batch) API Pricing**
| Resolution | Price (USD) |
| 1K | $0.08 |
| 2K | $0.08 |
| 4K | $0.12 |
#### Nano Banana Pro Text To Image(Batch) API Documentation
https://gateway.pixazo.ai/nano-banana-pro-async/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Text to Image - Nano Banana Pro Async API
**Request Code**
```
POST https://gateway.pixazo.ai/nano-banana-pro-async/v1/nano-banana-pro-text-to-image
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"prompt": "A cute robot"
}
```
**Output**
```
{
"request_id": "nano-banana-pro-async-api_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/nano-banana-pro-async-api_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Text to Image
| Parameter | Required | Type | Description |
| prompt | Yes | string | Text description of the desired image (required) |
| num_images | No | integer | Number of image variations to generate (1-10) |
| aspect_ratio | No | string | Image aspect ratio (see options below) |
| resolution | No | string | Image resolution: "1K", "2K", or "4K" |
| enable_web_search | No | boolean | Enable Google Search grounding for more accurate results |
| sync_mode | No | boolean | Return base64 data URIs instead of R2 URLs |
| webhook | No | string | Webhook URL for automatic completion notifications |
**Example Request**
```
{
"prompt": "A futuristic city on Mars with flying cars and neon lights at sunset",
"num_images": 4,
"aspect_ratio": "16:9",
"resolution": "4K",
"enable_web_search": true,
"sync_mode": false,
"webhook": "https://your-domain.com/api/webhook/gemini"
}
```
**Response**
```
{
"request_id": "nano-banana-pro-async-api_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/nano-banana-pro-async-api_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```
**Response Handling**

Common status codes.

| Code | Meaning |
| 202 | Accepted — Request queued |
| 400 | Bad Request |
| 401 | Unauthorized |
| 402 | Insufficient Balance |
| 403 | Forbidden |
| 429 | Too Many Requests |
| 500 | Internal Server Error |
**Error Responses**

Queue system errors and model validation errors.

Queue System Errors
// 402 — Insufficient balance
```
{
"error": "Insufficient Balance",
"message": "Your wallet does not have enough balance."
}
// 400 — Model not found
{
"error": "Model not found",
"message": "Model 'nano-banana-pro-async-api' not found or is disabled"
}
```
Error via Status/Webhook
```
{
"request_id": "nano-banana-pro-async-api_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "ERROR",
"model_id": "nano-banana-pro-async-api",
"error": "Description of the error",
"output": null
}
```
Retrieving Results

Poll the universal status endpoint to check progress and retrieve results.

Endpoint
```
GET https://gateway.pixazo.ai/v2/requests/status/{request_id}
Ocp-Apim-Subscription-Key: YOUR_API_KEY
```
cURL Example
```
curl -H "Ocp-Apim-Subscription-Key: YOUR_API_KEY" \
"https://gateway.pixazo.ai/v2/requests/status/nano-banana-pro-async-api_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
```
Response (Completed)
```
{
"request_id": "nano-banana-pro-async-api_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "COMPLETED",
"model_id": "nano-banana-pro-async-api",
"error": null,
"output": {
"media_url": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/v1/nano-banana-pro-async-api_019dxxxx-xxxx/output.ext"
],
"media_type": "application/octet-stream"
},
"created_at": "2026-03-31T10:00:00.000Z",
"updated_at": "2026-03-31T10:00:15.000Z",
"completed_at": "2026-03-31T10:00:15.000Z"
}
```
Response Fields
| Field | Type | Description |
| request_id | string | Unique request identifier |
| status | string | QUEUED, PROCESSING, COMPLETED, FAILED, or ERROR |
| model_id | string | Model that processed the request |
| error | string|null | Error message if failed |
| output.media_url | array | URLs to generated media (R2 CDN) |
| output.media_type | string | MIME type of the output |
| created_at | string | When request was created |
| completed_at | string|null | When request completed |
| polling_url | string | Status URL (initial response only) |
Status Values
| Status | Description |
| QUEUED | Request accepted, waiting to be processed |
| PROCESSING | Being processed by the model |
| COMPLETED | Done — output contains the result |
| FAILED | Failed — check error field |
| ERROR | System error — not charged |
Status Flow
QUEUED → PROCESSING → COMPLETED
→ FAILED
→ ERROR
Typical Workflow
Send a generate request to the API endpoint
Save the request_id from the response
Poll every 5-10 seconds: GET /v2/requests/status/{request_id}
When status is "COMPLETED", download from output.media_url

Tip: Use X-Webhook-URL header to get a callback instead of polling.

**Nano Banana Pro Text To Image(Batch) API Pricing**
| Resolution | Price (USD) |
| 1K | $0.08 |
| 2K | $0.08 |
| 4K | $0.12 |
#### 3. Nano Banana Standard
#### Nano Banana Standard Edit Image API Documentation
https://gateway.pixazo.ai/nano-banana/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Edit Image Request - Nano Banana API
**Request Code**
```
POST https://gateway.pixazo.ai/nano-banana/v1/nano-banana/generateEditImageRequest
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"prompt": "Add a sunset background to the beach photo",
"image_urls": [
"https://example.com/image1.jpg",
"https://example.com/image2.jpg"
],
"num_images": 1,
"output_format": "jpeg",
"sync_mode": false
}
```
**Output**
```
{
"request_id": "nano-banana_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/nano-banana_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Edit Image Request
| Parameter | Required | Type | Description |
| prompt | Yes | string | The prompt for image editing. Google's state-of-the-art image editing model that modifies existing images based on text descriptions |
| image_urls | Yes | array<string> | List of URLs of input images for editing. The images will be edited according to the provided prompt |
| num_images | Optional | integer | The number of edited images to generate |
| output_format | Optional | string | The format of the generated images. Values: "jpeg", "png" |
| sync_mode | Optional | boolean | When true, edited images will be returned as data URIs instead of URLs |
**Example Request**
```
{
"prompt": "Add a sunset background to the beach photo",
"image_urls": [
"https://example.com/image1.jpg",
"https://example.com/image2.jpg"
],
"num_images": 1,
"output_format": "jpeg",
"sync_mode": false
}
```
**Response**
```
{
"request_id": "nano-banana_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/nano-banana_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```
**Response Handling**

Common status codes.

| Code | Meaning |
| 202 | Accepted — Request queued |
| 400 | Bad Request |
| 401 | Unauthorized |
| 402 | Insufficient Balance |
| 403 | Forbidden |
| 429 | Too Many Requests |
| 500 | Internal Server Error |
**Error Responses**

Queue system errors and model validation errors.

Queue System Errors
// 402 — Insufficient balance
```
{
"error": "Insufficient Balance",
"message": "Your wallet does not have enough balance."
}
// 400 — Model not found
{
"error": "Model not found",
"message": "Model 'nano-banana' not found or is disabled"
}
```
Error via Status/Webhook
```
{
"request_id": "nano-banana_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "ERROR",
"model_id": "nano-banana",
"error": "Description of the error",
"output": null
}
```
Retrieving Results

Poll the universal status endpoint to check progress and retrieve results.

Endpoint
```
GET https://gateway.pixazo.ai/v2/requests/status/{request_id}
Ocp-Apim-Subscription-Key: YOUR_API_KEY
```
cURL Example
```
curl -H "Ocp-Apim-Subscription-Key: YOUR_API_KEY" \
"https://gateway.pixazo.ai/v2/requests/status/nano-banana_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
```
Response (Completed)
```
{
"request_id": "nano-banana_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "COMPLETED",
"model_id": "nano-banana",
"error": null,
"output": {
"media_url": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/v1/nano-banana_019dxxxx-xxxx/output.ext"
],
"media_type": "application/octet-stream"
},
"created_at": "2026-03-31T10:00:00.000Z",
"updated_at": "2026-03-31T10:00:15.000Z",
"completed_at": "2026-03-31T10:00:15.000Z"
}
```
Response Fields
| Field | Type | Description |
| request_id | string | Unique request identifier |
| status | string | QUEUED, PROCESSING, COMPLETED, FAILED, or ERROR |
| model_id | string | Model that processed the request |
| error | string|null | Error message if failed |
| output.media_url | array | URLs to generated media (R2 CDN) |
| output.media_type | string | MIME type of the output |
| created_at | string | When request was created |
| completed_at | string|null | When request completed |
| polling_url | string | Status URL (initial response only) |
Status Values
| Status | Description |
| QUEUED | Request accepted, waiting to be processed |
| PROCESSING | Being processed by the model |
| COMPLETED | Done — output contains the result |
| FAILED | Failed — check error field |
| ERROR | System error — not charged |
Status Flow
QUEUED → PROCESSING → COMPLETED
→ FAILED
→ ERROR
Typical Workflow
Send a generate request to the API endpoint
Save the request_id from the response
Poll every 5-10 seconds: GET /v2/requests/status/{request_id}
When status is "COMPLETED", download from output.media_url

Tip: Use X-Webhook-URL header to get a callback instead of polling.

**Nano Banana Standard Edit Image API Pricing**
| Resolution | Price (USD) |
| Per generation | $0.06 |
| Per generation | $0.1188 |
| Per generation | $0.1608 |
| Per generation | $0.1236 |
| Per generation | $0.1236 |
| Per generation | $0.0834 |
| Per generation | $0.0714 |
| Per generation | $0.0888 |
| Per generation | $0.0888 |
| Per generation | $0.1188 |
#### Nano Banana Standard Text To Image API Documentation
https://gateway.pixazo.ai/nano-banana/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Text To Image Request - Nano Banana API
**Request Code**
```
POST https://gateway.pixazo.ai/nano-banana/v1/nano-banana/generateTextToImageRequest
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"prompt": "An action shot of a black lab swimming in an inground suburban swimming pool. The camera is placed meticulously on the water line, dividing the image in half, revealing both the dogs head above water holding a tennis ball in it's mouth, and it's paws paddling underwater.",
"num_images": 1,
"limit_generations": false,
"output_format": "jpeg",
"aspect_ratio": "16:9",
"sync_mode": false
}
```
**Output**
```
{
"request_id": "nano-banana_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/nano-banana_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Text To Image Request
| Parameter | Required | Type | Description |
| prompt | Yes | string | The prompt for image generation. Google's state-of-the-art image generation model that generates high-quality images based on text descriptions |
| num_images | No | integer | The number of images to generate |
| limit_generations | No | boolean | Experimental parameter to limit the number of generations from each round of prompting to 1. Set to True to disregard any instructions in the prompt regarding the number of images to generate |
| output_format | No | string | The format of the generated images. Values: "jpeg", "png", "webp" |
| aspect_ratio | No | string | Aspect ratio for generated images. Values: "21:9", "1:1", "4:3", "3:2", "2:3", "5:4", "4:5", "3:4", "16:9", "9:16" |
| sync_mode | No | boolean | If True, the media will be returned as a data URI and the output data won't be available in the request history |
**Example Request**
```
{
"prompt": "An action shot of a black lab swimming in an inground suburban swimming pool. The camera is placed meticulously on the water line, dividing the image in half, revealing both the dogs head above water holding a tennis ball in it's mouth, and it's paws paddling underwater.",
"num_images": 1,
"limit_generations": false,
"output_format": "jpeg",
"aspect_ratio": "16:9",
"sync_mode": false
}
```
**Response**
```
{
"request_id": "nano-banana_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/nano-banana_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```
**Response Handling**

Common status codes.

| Code | Meaning |
| 202 | Accepted — Request queued |
| 400 | Bad Request |
| 401 | Unauthorized |
| 402 | Insufficient Balance |
| 403 | Forbidden |
| 429 | Too Many Requests |
| 500 | Internal Server Error |
**Error Responses**

Queue system errors and model validation errors.

Queue System Errors
// 402 — Insufficient balance
```
{
"error": "Insufficient Balance",
"message": "Your wallet does not have enough balance."
}
// 400 — Model not found
{
"error": "Model not found",
"message": "Model 'nano-banana' not found or is disabled"
}
```
Error via Status/Webhook
```
{
"request_id": "nano-banana_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "ERROR",
"model_id": "nano-banana",
"error": "Description of the error",
"output": null
}
```
Retrieving Results

Poll the universal status endpoint to check progress and retrieve results.

Endpoint
```
GET https://gateway.pixazo.ai/v2/requests/status/{request_id}
Ocp-Apim-Subscription-Key: YOUR_API_KEY
```
cURL Example
```
curl -H "Ocp-Apim-Subscription-Key: YOUR_API_KEY" \
"https://gateway.pixazo.ai/v2/requests/status/nano-banana_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
```
Response (Completed)
```
{
"request_id": "nano-banana_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "COMPLETED",
"model_id": "nano-banana",
"error": null,
"output": {
"media_url": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/v1/nano-banana_019dxxxx-xxxx/output.ext"
],
"media_type": "application/octet-stream"
},
"created_at": "2026-03-31T10:00:00.000Z",
"updated_at": "2026-03-31T10:00:15.000Z",
"completed_at": "2026-03-31T10:00:15.000Z"
}
```
Response Fields
| Field | Type | Description |
| request_id | string | Unique request identifier |
| status | string | QUEUED, PROCESSING, COMPLETED, FAILED, or ERROR |
| model_id | string | Model that processed the request |
| error | string|null | Error message if failed |
| output.media_url | array | URLs to generated media (R2 CDN) |
| output.media_type | string | MIME type of the output |
| created_at | string | When request was created |
| completed_at | string|null | When request completed |
| polling_url | string | Status URL (initial response only) |
Status Values
| Status | Description |
| QUEUED | Request accepted, waiting to be processed |
| PROCESSING | Being processed by the model |
| COMPLETED | Done — output contains the result |
| FAILED | Failed — check error field |
| ERROR | System error — not charged |
Status Flow
QUEUED → PROCESSING → COMPLETED
→ FAILED
→ ERROR
Typical Workflow
Send a generate request to the API endpoint
Save the request_id from the response
Poll every 5-10 seconds: GET /v2/requests/status/{request_id}
When status is "COMPLETED", download from output.media_url

Tip: Use X-Webhook-URL header to get a callback instead of polling.

**Nano Banana Standard Text To Image API Pricing**
| Resolution | Price (USD) |
| Per generation | $0.06 |
| Per generation | $0.1188 |
| Per generation | $0.1608 |
| Per generation | $0.1236 |
| Per generation | $0.1236 |
| Per generation | $0.0834 |
| Per generation | $0.0714 |
| Per generation | $0.0888 |
| Per generation | $0.0888 |
| Per generation | $0.1188 |
#### Nano Banana Standard Image To Image Edit(Batch) API Documentation
https://gateway.pixazo.ai/nano-banana-async/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Image to Image(Edit Image) - Nano Banana Async API
**Request Code**
```
POST https://gateway.pixazo.ai/nano-banana-async/v1/nano-banana-image-to-image
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"prompt": "Convert to watercolor",
"image_urls": ["https://example.com/image.jpg"]
}
```
**Output**
```
{
"request_id": "nano-banana-async-api_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/nano-banana-async-api_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Image to Image(Edit Image)
| Parameter | Required | Type | Description |
| prompt | Yes | string | Text description of the desired transformation (required) |
| image_urls | Yes | string[] | Array of input image URLs (1-3 images, required) |
| num_images | No | integer | Number of image variations to generate (1-10) |
| aspect_ratio | No | string | Image aspect ratio (see options below) |
| output_format | No | string | Output format: "jpeg", "png", or "webp" |
| resolution | No | string | Image resolution: "1K" or "2K" |
| enable_web_search | No | boolean | Enable Google Search grounding for more accurate results |
| sync_mode | No | boolean | Return base64 data URIs instead of R2 URLs |
| webhook | No | string | Webhook URL for automatic completion notifications |
**Example Request**
```
{
"prompt": "Transform into a detailed watercolor painting with soft brush strokes and vibrant colors",
"image_urls": [
"https://example.com/image1.jpg",
"https://example.com/image2.jpg",
"https://example.com/image3.jpg"
],
"num_images": 1,
"aspect_ratio": "21:9",
"output_format": "png",
"resolution": "2K",
"enable_web_search": true,
"sync_mode": false,
"webhook": "https://your-domain.com/api/webhook/gemini"
}
```
**Response**
```
{
"request_id": "nano-banana-async-api_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/nano-banana-async-api_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```
**Response Handling**

Common status codes.

| Code | Meaning |
| 202 | Accepted — Request queued |
| 400 | Bad Request |
| 401 | Unauthorized |
| 402 | Insufficient Balance |
| 403 | Forbidden |
| 429 | Too Many Requests |
| 500 | Internal Server Error |
**Error Responses**

Queue system errors and model validation errors.

Queue System Errors
// 402 — Insufficient balance
```
{
"error": "Insufficient Balance",
"message": "Your wallet does not have enough balance."
}
// 400 — Model not found
{
"error": "Model not found",
"message": "Model 'nano-banana-async-api' not found or is disabled"
}
```
Error via Status/Webhook
```
{
"request_id": "nano-banana-async-api_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "ERROR",
"model_id": "nano-banana-async-api",
"error": "Description of the error",
"output": null
}
```
Retrieving Results

Poll the universal status endpoint to check progress and retrieve results.

Endpoint
```
GET https://gateway.pixazo.ai/v2/requests/status/{request_id}
Ocp-Apim-Subscription-Key: YOUR_API_KEY
```
cURL Example
```
curl -H "Ocp-Apim-Subscription-Key: YOUR_API_KEY" \
"https://gateway.pixazo.ai/v2/requests/status/nano-banana-async-api_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
```
Response (Completed)
```
{
"request_id": "nano-banana-async-api_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "COMPLETED",
"model_id": "nano-banana-async-api",
"error": null,
"output": {
"media_url": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/v1/nano-banana-async-api_019dxxxx-xxxx/output.ext"
],
"media_type": "application/octet-stream"
},
"created_at": "2026-03-31T10:00:00.000Z",
"updated_at": "2026-03-31T10:00:15.000Z",
"completed_at": "2026-03-31T10:00:15.000Z"
}
```
Response Fields
| Field | Type | Description |
| request_id | string | Unique request identifier |
| status | string | QUEUED, PROCESSING, COMPLETED, FAILED, or ERROR |
| model_id | string | Model that processed the request |
| error | string|null | Error message if failed |
| output.media_url | array | URLs to generated media (R2 CDN) |
| output.media_type | string | MIME type of the output |
| created_at | string | When request was created |
| completed_at | string|null | When request completed |
| polling_url | string | Status URL (initial response only) |
Status Values
| Status | Description |
| QUEUED | Request accepted, waiting to be processed |
| PROCESSING | Being processed by the model |
| COMPLETED | Done — output contains the result |
| FAILED | Failed — check error field |
| ERROR | System error — not charged |
Status Flow
QUEUED → PROCESSING → COMPLETED
→ FAILED
→ ERROR
Typical Workflow
Send a generate request to the API endpoint
Save the request_id from the response
Poll every 5-10 seconds: GET /v2/requests/status/{request_id}
When status is "COMPLETED", download from output.media_url

Tip: Use X-Webhook-URL header to get a callback instead of polling.

**Nano Banana Standard Image To Image Edit(Batch) API Pricing**
| Resolution | Price (USD) |
| 1K | $0.03 |
| 2K | $0.05 |

---

### Reve Image 1.0 API - AI Image Generation & Editing APIs
**Page:** https://www.pixazo.ai/models/reve-image


by Reve

Reve Image 1.0 API, developers can implement comprehensive image manipulation features that go beyond simple generation. The API supports image editing workflows where users can modify and remix existing visuals, making it ideal for creative tools and content transformation applications.

Models Version
Reve Image Generation
Image Edit
Image Remix
Image Edit
**Request Code**
**Request Parameters**
**Example Request**
**Response**
**Request Headers**
**Response Handling**
**Pricing**
Image Remix
#### Reve Image Generation Image Edit API Documentation
https://gateway.pixazo.ai/reve-image/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Image Edit - Reve Image generation API
**Request Code**
```
POST https://gateway.pixazo.ai/reve-image/v1/reve-edit/generate
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"image": "https://example.com/photo.jpg",
"prompt": "Add \"HELLO WORLD\" text in the middle of this image in a modern font, white text"
}
```
**Output**
```
{
"request_id": "reve-image-generation_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/reve-image-generation_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Image Edit
| Parameter | Required | Type | Description |
| image | Yes | string | Input image URL to edit. Must be publicly accessible (HTTPS recommended). |
| prompt | Yes | string | Text instruction describing the edit you want to apply. Be specific and clear. |
| version | No | string | Model version. Valid values: latest, reve-edit@20250915. |
| webhook | No | string | Callback URL for completion notification. POST request sent with results when complete. |
| webhook_events_filter | No | array | Events that trigger webhook. Valid values: ["*"] (all), ["completed"] (success/failure only). |
**Example Request**
```
{
"image": "https://example.com/photo.jpg",
"prompt": "Add \"HELLO WORLD\" text in the middle of this image in a modern font, white text"
}
```
**Response**
```
{
"request_id": "reve-image-generation_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/reve-image-generation_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
```
POST https://gateway.pixazo.ai/reve-image/v1/reve-edit/prediction
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"prediction_id": "xyz789abc123def456ghi789jkl012mno"
}
```
**Output**
```
{
"success": true,
"id": "xyz789abc123...",
"status": "succeeded",
"output": "https://.../reve-edit/xyz789abc123_output_0.png"
}
```
Example Response (In Progress)
```
{
"success": true,
"id": "xyz789abc123def456ghi789jkl012mno",
"model": "reve/edit",
"status": "processing",
"input": {
"image": "https://example.com/photo.jpg",
"prompt": "Add \"HELLO\" text in the middle",
"version": "latest"
},
"created_at": "2025-10-23T14:30:00.000Z"
}
```
Example Response (Completed)
```
{
"success": true,
"id": "xyz789abc123def456ghi789jkl012mno",
"model": "reve/edit",
"status": "succeeded",
"input": {
"image": "https://example.com/photo.jpg",
"prompt": "Add \"HELLO\" text in the middle",
"version": "latest"
},
"output": "https://.../reve-edit/xyz789abc123def456ghi789jkl012mno_output_0.png",
"created_at": "2025-10-23T14:30:00.000Z"
}
```
**Response Handling**

Common status codes.

| Code | Meaning |
| 202 | Accepted — Request queued |
| 400 | Bad Request |
| 401 | Unauthorized |
| 402 | Insufficient Balance |
| 403 | Forbidden |
| 429 | Too Many Requests |
| 500 | Internal Server Error |
**Error Responses**

Queue system errors and model validation errors.

Queue System Errors
// 402 — Insufficient balance
```
{
"error": "Insufficient Balance",
"message": "Your wallet does not have enough balance."
}
// 400 — Model not found
{
"error": "Model not found",
"message": "Model 'reve-image-generation' not found or is disabled"
}
```
Error via Status/Webhook
```
{
"request_id": "reve-image-generation_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "ERROR",
"model_id": "reve-image-generation",
"error": "Description of the error",
"output": null
}
```
Retrieving Results

Poll the universal status endpoint to check progress and retrieve results.

Endpoint
```
GET https://gateway.pixazo.ai/v2/requests/status/{request_id}
Ocp-Apim-Subscription-Key: YOUR_API_KEY
```
cURL Example
```
curl -H "Ocp-Apim-Subscription-Key: YOUR_API_KEY" \
"https://gateway.pixazo.ai/v2/requests/status/reve-image-generation_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
```
Response (Completed)
```
{
"request_id": "reve-image-generation_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "COMPLETED",
"model_id": "reve-image-generation",
"error": null,
"output": {
"media_url": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/v1/reve-image-generation_019dxxxx-xxxx/output.ext"
],
"media_type": "application/octet-stream"
},
"created_at": "2026-03-31T10:00:00.000Z",
"updated_at": "2026-03-31T10:00:15.000Z",
"completed_at": "2026-03-31T10:00:15.000Z"
}
```
Response Fields
| Field | Type | Description |
| request_id | string | Unique request identifier |
| status | string | QUEUED, PROCESSING, COMPLETED, FAILED, or ERROR |
| model_id | string | Model that processed the request |
| error | string|null | Error message if failed |
| output.media_url | array | URLs to generated media (R2 CDN) |
| output.media_type | string | MIME type of the output |
| created_at | string | When request was created |
| completed_at | string|null | When request completed |
| polling_url | string | Status URL (initial response only) |
Status Values
| Status | Description |
| QUEUED | Request accepted, waiting to be processed |
| PROCESSING | Being processed by the model |
| COMPLETED | Done — output contains the result |
| FAILED | Failed — check error field |
| ERROR | System error — not charged |
Status Flow
QUEUED → PROCESSING → COMPLETED
→ FAILED
→ ERROR
Typical Workflow
Send a generate request to the API endpoint
Save the request_id from the response
Poll every 5-10 seconds: GET /v2/requests/status/{request_id}
When status is "COMPLETED", download from output.media_url

Tip: Use X-Webhook-URL header to get a callback instead of polling.

**Reve Image Generation Image Edit API Pricing**
| Resolution | Price (USD) |
| All Resolution | $0.06 |
#### Reve Image Generation Image Remix API Documentation
https://gateway.pixazo.ai/reve-image/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Image Remix - Reve Image generation API
**Request Code**
```
POST https://gateway.pixazo.ai/reve-image/v1/reve-remix/generate
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"prompt": "A beautiful sunset over mountains with vibrant colors",
"reference_images": [
"https://example.com/city-photo.jpg"
],
"aspect_ratio": "16:9"
}
```
**Output**
```
{
"request_id": "reve-image-generation_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/reve-image-generation_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Image Remix
| Parameter | Required | Type | Description |
| prompt | Yes | string | Text description for the remixing task. Be specific and descriptive for best results. |
| reference_images | No | array | Array of 1-4 image URLs to use as references. Must be publicly accessible (HTTPS recommended). |
| aspect_ratio | No | string | Output aspect ratio. Valid values: `16:9`, `9:16`, `3:2`, `2:3`, `4:3`, `3:4`, `1:1`. |
| version | No | string | Model version. Valid values: `latest`, `reve-remix@20250915`. |
| webhook | No | string | Callback URL for completion notification. POST request sent with results when complete. |
| webhook_events_filter | No | array | Events that trigger webhook. Valid values: `["*"]` (all), `["completed"]` (success/failure only). |
**Example Request**
```
{
"prompt": "A beautiful sunset over mountains with vibrant colors",
"reference_images": [
"https://example.com/city-photo.jpg"
],
"aspect_ratio": "16:9"
}
```
**Response**
```
{
"request_id": "reve-image-generation_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/reve-image-generation_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```
**Response Handling**

Common status codes.

| Code | Meaning |
| 202 | Accepted — Request queued |
| 400 | Bad Request |
| 401 | Unauthorized |
| 402 | Insufficient Balance |
| 403 | Forbidden |
| 429 | Too Many Requests |
| 500 | Internal Server Error |
**Error Responses**

Queue system errors and model validation errors.

Queue System Errors
// 402 — Insufficient balance
```
{
"error": "Insufficient Balance",
"message": "Your wallet does not have enough balance."
}
// 400 — Model not found
{
"error": "Model not found",
"message": "Model 'reve-image-generation' not found or is disabled"
}
```
Error via Status/Webhook
```
{
"request_id": "reve-image-generation_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "ERROR",
"model_id": "reve-image-generation",
"error": "Description of the error",
"output": null
}
```
Retrieving Results

Poll the universal status endpoint to check progress and retrieve results.

Endpoint
```
GET https://gateway.pixazo.ai/v2/requests/status/{request_id}
Ocp-Apim-Subscription-Key: YOUR_API_KEY
```
cURL Example
```
curl -H "Ocp-Apim-Subscription-Key: YOUR_API_KEY" \
"https://gateway.pixazo.ai/v2/requests/status/reve-image-generation_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
```
Response (Completed)
```
{
"request_id": "reve-image-generation_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "COMPLETED",
"model_id": "reve-image-generation",
"error": null,
"output": {
"media_url": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/v1/reve-image-generation_019dxxxx-xxxx/output.ext"
],
"media_type": "application/octet-stream"
},
"created_at": "2026-03-31T10:00:00.000Z",
"updated_at": "2026-03-31T10:00:15.000Z",
"completed_at": "2026-03-31T10:00:15.000Z"
}
```
Response Fields
| Field | Type | Description |
| request_id | string | Unique request identifier |
| status | string | QUEUED, PROCESSING, COMPLETED, FAILED, or ERROR |
| model_id | string | Model that processed the request |
| error | string|null | Error message if failed |
| output.media_url | array | URLs to generated media (R2 CDN) |
| output.media_type | string | MIME type of the output |
| created_at | string | When request was created |
| completed_at | string|null | When request completed |
| polling_url | string | Status URL (initial response only) |
Status Values
| Status | Description |
| QUEUED | Request accepted, waiting to be processed |
| PROCESSING | Being processed by the model |
| COMPLETED | Done — output contains the result |
| FAILED | Failed — check error field |
| ERROR | System error — not charged |
Status Flow
QUEUED → PROCESSING → COMPLETED
→ FAILED
→ ERROR
Typical Workflow
Send a generate request to the API endpoint
Save the request_id from the response
Poll every 5-10 seconds: GET /v2/requests/status/{request_id}
When status is "COMPLETED", download from output.media_url

Tip: Use X-Webhook-URL header to get a callback instead of polling.

**Reve Image Generation Image Remix API Pricing**
| Resolution | Price (USD) |
| All Resolution | $0.06 |

---

### Studio Ghibli API - AI Anime Image Generation API
**Page:** https://www.pixazo.ai/models/studio-ghibli


by Ghibli

Studio Ghibli API, developers can transform text prompts and images into lush, hand-painted-looking scenes with soft lighting, vibrant colors, and the whimsical charm that defines Ghibli animation. The API supports both text-to-image and image-to-image workflows, making it ideal for creative projects, social media content, and applications seeking a distinctive anime art style.

Models Version
Studio Ghibli v1
Text To Image
Text To Image
**Request Code**
**Request Parameters**
**Example Request**
**Response**
**Request Headers**
**Response Handling**
**Pricing**
#### Studio Ghibli v1 Text To Image API Documentation
https://gateway.pixazo.ai/studio-ghibli/v1
**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Generate Image Request - Studio Ghibli API
**Request Code**
```
POST https://gateway.pixazo.ai/studio-ghibli/v1/studio-ghibli/generate
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"prompt": "A peaceful village in the mountains at sunset, Studio Ghibli style"
}
```
**Output**
```
{
"request_id": "studio-ghibli_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/studio-ghibli_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Webhook (Optional)**

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Generate Image Request
| Parameter | Required | Type | Description |
| prompt | Yes | string | Text description of the image you want to generate. Be specific and descriptive for best results. |
| negative_prompt | No | string | What to exclude from the image (e.g., "people, modern buildings, text, watermark"). Helps refine output quality. |
| image | No | string | Image URL for img2img or inpainting mode. Must be publicly accessible (HTTPS recommended). |
| mask | No | string | Mask URL for inpainting mode. White areas = regenerate, black areas = preserve. Requires `image` parameter. |
| aspect_ratio | No | string | Output aspect ratio. Valid values: `1:1`, `16:9`, `21:9`, `3:2`, `2:3`, `4:5`, `5:4`, `3:4`, `4:3`, `9:16`, `9:21`. Cannot be used with `width`/`height`. |
| width | No | integer | Custom output width in pixels (256-2048). Use instead of `aspect_ratio` for precise sizing. |
| height | No | integer | Custom output height in pixels (256-2048). Use instead of `aspect_ratio` for precise sizing. |
| output_format | No | string | Output image format. Valid values: `webp` (smallest), `jpg`, `png`. |
| output_quality | No | integer | Output quality (0-100). Higher = better quality but larger file size. |
| num_outputs | No | integer | Number of image variations to generate (1-4). Each variation is unique. |
| webhook | No | string | Callback URL for completion notification. POST request sent with generation results when complete. |
| webhook_events_filter | No | array | Events that trigger webhook. Valid values: `["*"]` (all), `["completed"]` (success/failure only), `["start", "output", "completed"]`. |
**Example Request**
```
{
"prompt": "A peaceful village in the mountains at sunset, Studio Ghibli style",
"negative_prompt": "people, modern buildings, cars, text, watermark, realistic photo",
"aspect_ratio": "16:9",
"output_format": "png",
"output_quality": 95
}
```
**Response**
```
{
"request_id": "studio-ghibli_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/studio-ghibli_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
**Request Headers**
| Header | Value |
```
Content-Type	application/json
Cache-Control	no-cache
Ocp-Apim-Subscription-Key	YOUR_SUBSCRIPTION_KEY
```
**Response Handling**

Common status codes.

| Code | Meaning |
| 202 | Accepted — Request queued |
| 400 | Bad Request |
| 401 | Unauthorized |
| 402 | Insufficient Balance |
| 403 | Forbidden |
| 429 | Too Many Requests |
| 500 | Internal Server Error |
**Error Responses**

Queue system errors and model validation errors.

Queue System Errors
// 402 — Insufficient balance
```
{
"error": "Insufficient Balance",
"message": "Your wallet does not have enough balance. Required: $0.01"
}
// 400 — Model not found
{
"error": "Model not found",
"message": "Model 'studio-ghibli' not found or is disabled"
}
```
Error via Status/Webhook
```
{
"request_id": "studio-ghibli_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "ERROR",
"model_id": "studio-ghibli",
"error": "Description of the error",
"output": null
}
```
Retrieving Results

Poll the universal status endpoint to check progress and retrieve results.

Endpoint
```
GET https://gateway.pixazo.ai/v2/requests/status/{request_id}
Ocp-Apim-Subscription-Key: YOUR_API_KEY
```
cURL Example
```
curl -H "Ocp-Apim-Subscription-Key: YOUR_API_KEY" \
"https://gateway.pixazo.ai/v2/requests/status/studio-ghibli_019d42ce-946d-7739-f812-6875c434cb790"
```
Response (Completed)
```
{
"request_id": "studio-ghibli_019d42ce-946d-7739-f812-6875c434cb790",
"status": "COMPLETED",
"model_id": "studio-ghibli",
"error": null,
"output": {
"media_url": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/v1/studio-ghibli_019d42ce-946d-7739-f812-6875c434cb790/output.png"
],
"media_type": "image/png"
},
"created_at": "2026-03-31T07:32:03.749Z",
"updated_at": "2026-03-31T07:32:20.000Z",
"completed_at": "2026-03-31T07:32:20.000Z"
}
```
Response Fields
| Field | Type | Description |
| request_id | string | Unique request identifier |
| status | string | QUEUED, PROCESSING, COMPLETED, FAILED, or ERROR |
| model_id | string | Model that processed the request |
| error | string|null | Error message if failed |
| output.media_url | array | URLs to generated media (R2 CDN) |
| output.media_type | string | MIME type (image/png) |
| created_at | string | When request was created |
| completed_at | string|null | When request completed |
| polling_url | string | Status URL (initial response only) |
Status Values
| Status | Description |
| QUEUED | Request accepted, waiting to be processed |
| PROCESSING | Being processed by the model |
| COMPLETED | Done — output contains the result |
| FAILED | Failed — check error field |
| ERROR | System error — not charged |
Status Flow
QUEUED → PROCESSING → COMPLETED
→ FAILED
→ ERROR
Typical Workflow
Send a generate request to the API endpoint
Save the request_id from the response
Poll every 5-10 seconds: GET /v2/requests/status/{request_id}
When status is "COMPLETED", download from output.media_url

Tip: Use X-Webhook-URL header to get a callback instead of polling.

**Studio Ghibli v1 Text To Image API Pricing**
| Resolution | Price (USD) |
| Per generation | $0.015 |
| Per generation | $0.0297 |
| Per generation | $0.0402 |
| Per generation | $0.0309 |
| Per generation | $0.0309 |
| Per generation | $0.0208 |
| Per generation | $0.0179 |
| Per generation | $0.0222 |
| Per generation | $0.0222 |
| Per generation | $0.0297 |
| Per generation | $0.039 |

---
