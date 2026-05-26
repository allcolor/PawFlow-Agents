# Pixazo API — Complete Model Reference

> Auto-generated from [pixazo.ai/models](https://www.pixazo.ai/models) on 2026-04-15.
> 57 models, ~198 API endpoints.

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
| Hailuo v2.3 | Image/Text to Video | 6s | $0.35 |
| Hailuo v2.3 | Image/Text to Video | 10s | $0.60 |
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
| Luma Ray 2 Flash | Image to Video | per second | $0.04 |
| Kling O3 | Text/Image to Image | 4K | $0.056 |
| Kling 3.0 | Text/Image to Video | 1s (no audio) | $0.168 |
| Kling 3.0 | Text/Image to Video | 1s (audio) | $0.252 |
| Kling v1.6 | Text/Image to Video | 1s | $0.07 |
| Kling Avatar v2 | AI Avatar | 1s | $0.115 |
| Kling O1 | Ref Image to Video | per gen | $0.90 |
| Lyria 3 Pro | Music Generation | per gen | $0.08 |
| VibeVoice | Text to Speech | 480p | $0.75 |
| VibeVoice | Text to Speech | 580p | $1.00 |
| VibeVoice | Text to Speech | 720p | $1.25 |
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
| Studio Ghibli | Image Gen | varies | $0.01-0.04 |
| Veo 3.1 Fast | Video Gen | 1s (audio) | $0.15 |
| Veo 3.1 Fast | Video Gen | 1s (no audio) | $0.10 |
| Veo 3.1 | Video Gen | 1s | $1.80-3.60 |
| Wan 2.7 | Text/Edit Image | per gen | $0.03 |
| Wan 2.7 Pro | Text/Edit Image | per gen | $0.075 |
| Wan 2.6 | Image/Text to Video | 5s | $0.75 |
| Wan 2.6 | Image/Text to Video | 10s | $1.50 |
| Wan 2.5 | All operations | per gen | $0.05 |
| Ace Step 1.5 XL | Music Gen | per gen | $0.015 |
| Auraflow v0.3 | Text to Image | all res | $0.001 |
| Bria RMBG 2.0 | BG Removal | all res | $0.018 |
| Chatterbox | TTS | all res | $0.03 |
| Crystal Upscaler | Image Upscale | per gen | $0.07-0.45 |
| DALL-E | Image Gen | - | coming soon |
| GenFlare 2.0 | Image to Video | per gen | not listed |
| Grok Imagine | Text to Image | all res | $0.018 |
| Grok Imagine | Text to Video | 480p/1s | $0.05 |
| Grok Imagine | Text to Video | 720p/1s | $0.07 |
| Higgsfield DoP | Video Gen | per gen | $0.15-0.37 |
| Hyper3D Rodin | 3D Gen | per gen | $0.40 |
| IDM VTON | Virtual Try-On | all res | $0.05 |
| Kandinsky 5 Pro | Image to Video | 512p/1s | $0.04 |
| Kandinsky 5 Pro | Image to Video | 1024p/1s | $0.12 |
| LongCat Image | Text to Image | per gen | $0.13 |
| LTX Video | Text to Image | all res | $0.09 |
| LTX Video | Video Gen | 1080p/1s | $0.06 |
| LTX Video | Video Gen | 1440p/1s | $0.12 |
| LTX Video | Video Gen | 2160p/1s | $0.24 |
| Lucy Edit | Video Edit | all res | $0.20 |
| MiniMax | Music Gen | per 1K chars | $0.10 |
| MiniMax | Voice Design | per 1K chars | $0.03 |
| MiniMax | Image Gen | all res | $0.02 |
| Mochi v1 | Video Gen | per gen | $0.40 |
| OmniHuman 1.5 | Lipsync | per second | $0.16 |
| Pixelforge | Image Gen | all res | $0.04 |
| PixVerse | Video Gen | 360p/1s | $0.025-0.035 |
| PixVerse | Video Gen | 720p/1s | $0.045-0.06 |
| PixVerse | Video Gen | 1080p/1s | $0.09-0.115 |
| Qwen Image Max | Image Gen/Edit | all res | $0.04-0.055 |
| SDXL | Image Gen | all res | Free |
| SeedVR Upscale | Image/Video Upscale | per gen | Free-$0.001 |
| Stable Diffusion 3.5 | Text to Image | all res | $0.20 |
| Stable Diffusion | Classic models | all res | Free |
| Topaz | Video Upscale | per gen | not listed |
| Tracks | Music Gen | per gen | not listed |
| Trellis 3D | 3D Gen | all res | $0.35 |
| Tripo3D v2.5 | 3D Gen | per gen | not listed |
| VEED Fabric | Video Gen | per gen | not listed |
| VEED | BG Removal | per gen | not listed |
| Vidu | Video Gen | per gen | not listed |
| XTTS v2 | Voice Clone TTS | per gen | not listed |
| Z-Image Turbo | Text to Image | all res | $0.008 |
| Z-Image Base | Text to Image | all res | $0.01 |

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
| hailuo | `minimax-hailuo-ai` | `imageToVideo` | POST |
| hailuo | `minimax-hailuo-ai` | `generate` | POST |
| luma | `luma-dream-machine-ray-2-flash-image-to-video` | `luma-dream-machine-ray-2-flash-image-to-video-request` | POST |
| vibevoice | `vibevoice` | `vibevoice/generateRequest` | POST |
| vibevoice | `vibevoice-realtime-0-5b-135` | `vibevoice-realtime-0-5b-request` | POST |
| ace-step | `ace-step-xl` | `submitMusicGenerationRequest` | POST |
| ace-step | `ace-step` | `generate` | POST |
| auraflow | `auraflow-v0-3-512` | `auraflow-v0-3-request` | POST |
| bria | `bria-rmbg-2-0-682` | `bria-rmbg-2-0-request` | POST |
| chatterbox | `chatterbox-text-to-speech` | `chatterbox-text-to-speech-request` | POST |
| crystal-upscaler | `upscaler` | `crystal-upscaler/generate` | POST |
| genflare | `baidu-genflare-2-0-api` | `generateImageToVideo2-5Request` | POST |
| grok-imagine | `grok-imagine-api-641` | `grok-imagine-api-request` | POST |
| grok-imagine | `grok-imagine-video` | `grok-imagine-video-request` | POST |
| higgsfield | `ai-model-api` | `generateSoul` | POST |
| higgsfield | `ai-model-api` | `generateImageToVideoRequest` | POST |
| hyper3d | `hyper3d-rodin-259` | `hyper3d-rodin-request` | POST |
| idm-vton | `idm-vton-api` | `r-idm-vton` | POST |
| kandinsky | `kandinsky-5-0-pro-953` | `kandinsky-5-0-pro-request` | POST |
| longcat-image | `longcat-image-498` | `longcat-image-request` | POST |
| ltx | `lightricks` | `ltx/generate` | POST |
| ltx | `ltx-2-19b-api-513` | `ltx-2-19b-api-request` | POST |
| ltx | `ltx-2-video-api-581` | `ltx-2-video-api-request` | POST |
| lucy-edit | `decart-lucy-edit-video-fast-142` | `decart-lucy-edit-video-fast-request` | POST |
| minimax | `minimax-hailuo-ai-music` | `getAudio` | POST |
| minimax | `minimax-hailuo-ai-music` | `getAudioResult` | POST |
| minimax | `image-generation` | `i2i` | POST |
| minimax | `image-generation` | `t2i` | POST |
| mochi | `mochi-v1-clone` | `generate` | POST |
| omnihuman | `bytedance-omnihuman-v1-5-290` | `bytedance-omnihuman-v1-5-request` | POST |
| pixelforge | `pixelforge-image` | `qwen_image_gen/serve_image` | POST |
| pixelforge | `pixelforge-relighting-api` | `relighting/generate` | POST |
| pixverse | `pixverse-v6-image-to-video` | various | POST |
| pixverse | `pixverse` | various | POST |
| pixverse | `pixverse-i2v` | `pixverse-i2v-request` | POST |
| qwen-image | `qwen-image-max-edit` | `qwen-image-max-edit-request` | POST |
| qwen-image | `qwen-image-max` | `qwen-image-max-request` | POST |
| qwen-image | `qwen-image` | `generateMultimodeTextToImageEditRequest` | POST |
| qwen-image | `qwen-image-edit-plus` | `qwen-image-edit-plus-lora/generate` | POST |
| qwen-image | `qwen-image-edit-plus-trainer` | `qwen-image-edit-plus-trainer/generate` | POST |
| qwen-image | `qwen-image-layered` | `qwen-image-layered-request` | POST |
| sdxl | `sdxlTurbo` | `getData` | POST |
| sdxl | `getImage` | `getSDXLImage` | POST |
| sdxl | `sdxl_lightning` | `getSDXLImage` | POST |
| seedvr | `seedvr-upscale` | `upscale-image/generate` | POST |
| seedvr | `seedvr-upscale` | `upscale-video/generate` | POST |
| stable-diffusion | `sd3-5` | `r-sd-3-5-large` | POST |
| stable-diffusion | `sd3` | `getData` | POST |
| stable-diffusion | `inpainting` | `getImage` | POST |
| topaz | `topaz-upscale-video-753` | `topaz-upscale-video-request` | POST |
| tracks | `tracks` | `generate` | POST |
| trellis3d | `trellis-2-image-to-3d` | `trellis-2-image-to-3d-request` | POST |
| tripo3d | `tripo3d-v2-5-413` | `tripo3d-v2-5-request` | POST |
| veed | `veed-fabric-1-0-api-130` | various | POST |
| veed | `veed-video-background-remover-541` | `veed-video-background-remover-request` | POST |
| vidu | `vidu` | `vidu-request` | POST |
| vidu | `vidu-q2-reference-to-video-pro-api-454` | `vidu-q2-reference-to-video-pro-api-request` | POST |
| xtts | `voice-clone` | `xtts-v2/generate` | POST |
| z-image | `z-image-turbo-834` | `z-image-turbo-request` | POST |
| z-image | `z-image-base` | `z-image-base-request` | POST |

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

### Hailuo 2.3 API - AI Video Generation APIs
**Page:** https://www.pixazo.ai/models/hailuo

> by MiniMax. Generate videos from text descriptions or images with support for various styles and durations.

#### 1. Hailuo v2.3 — Image To Video

**Endpoint:**
```
POST https://gateway.pixazo.ai/minimax-hailuo-ai/v1/imageToVideo
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
  "prompt": "Man walked into winter cave with polar bear",
  "first_frame_image": "https://example.com/image.jpg",
  "duration": 6,
  "resolution": "768P",
  "prompt_optimizer": true,
  "fast_pretreatment": false,
  "aigc_watermark": false
}
```

**Parameters:**

| Parameter | Required | Type | Description |
|-----------|----------|------|-------------|
| prompt | Yes | string | Video description, max 2000 chars. Supports 15+ camera movement instructions |
| first_frame_image | Yes | string | Image URL for starting frame. JPG/JPEG/PNG/WebP, <20MB, short side >300px, aspect ratio 2:5 to 5:2 |
| prompt_optimizer | No | boolean | Default: true. Auto-optimizes prompt for better quality |
| fast_pretreatment | No | boolean | Default: false. Shortens optimization time (MiniMax-Hailuo-2.3-Fast only) |
| duration | No | integer | Default: 6. Video duration: 6 or 10 seconds |
| resolution | No | string | Default: "768P". Options: "768P", "1080P" |
| callback_url | No | string | Webhook URL for status updates |
| aigc_watermark | No | boolean | Default: false. Add AIGC watermark |

**Response:**
```json
{
  "request_id": "minimax-hailuo-ai_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "status": "QUEUED",
  "polling_url": "https://gateway.pixazo.ai/v2/requests/status/{request_id}"
}
```

**Pricing:**

| Resolution | Duration | Price (USD) |
|------------|----------|-------------|
| All | 6s | $0.35 |
| All | 10s | $0.60 |

---

#### 2. Hailuo v2.3 — Text To Video

**Endpoint:**
```
POST https://gateway.pixazo.ai/minimax-hailuo-ai/v1/generate
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
  "prompt": "A high-energy scene of a bear leaping into a river to catch a fish...",
  "duration": 6,
  "resolution": "768P",
  "prompt_optimizer": true,
  "fast_pretreatment": false,
  "aigc_watermark": false
}
```

**Parameters:**

| Parameter | Required | Type | Description |
|-----------|----------|------|-------------|
| prompt | Yes | string | Scene description for video generation |
| duration | No | number | Duration in seconds |
| resolution | No | string | Video resolution |
| prompt_optimizer | No | boolean | Auto-refines prompt for better quality |
| fast_pretreatment | No | boolean | Enable fast pretreatment |
| callback_url | No | string | Webhook URL for notifications |
| aigc_watermark | No | boolean | Add AIGC watermark |

**Response:** Same async pattern — `request_id` + `polling_url`

**Pricing:** Same as Image To Video ($0.35/6s, $0.60/10s)

---

### Luma Dream Machine 1.5 API, Ray 2 Flash API - AI Video Generation APIs
**Page:** https://www.pixazo.ai/models/luma

> by Luma AI. Generate videos from text prompts or images with exceptional visual quality and temporal consistency.

#### Luma Dream Machine Ray 2 Flash — Image To Video

**Endpoint:**
```
POST https://gateway.pixazo.ai/luma-dream-machine-ray-2-flash-image-to-video/v1/luma-dream-machine-ray-2-flash-image-to-video-request
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
  "prompt": "Create a magical timelapse transition...",
  "image_url": "https://example.com/image.jpg",
  "end_image_url": "",
  "aspect_ratio": "16:9",
  "loop": false,
  "resolution": "540p",
  "duration": "5s"
}
```

**Parameters:**

| Parameter | Required | Type | Default | Description |
|-----------|----------|------|---------|-------------|
| prompt | Yes | string | — | Detailed text description guiding the video transformation |
| image_url | Yes | string | — | Publicly accessible URL of starting image (JPEG, PNG, WebP) |
| end_image_url | No | string | "" | URL of ending image for transitions |
| aspect_ratio | No | string | "16:9" | Options: "16:9", "9:16", "4:3", "3:4", "21:9", "9:21" |
| loop | No | boolean | false | Seamless loop for background animations |
| resolution | No | string | "540p" | Options: "540p", "720p", "1080p" |
| duration | No | string | "5s" | Options: "5s", "9s" |

**Response:**
```json
{
  "request_id": "luma-dream-machine-ray-2-flash-image-to-video_019dxxxx-xxxx",
  "status": "QUEUED",
  "polling_url": "https://gateway.pixazo.ai/v2/requests/status/{request_id}"
}
```

**Pricing:** $0.04 per second of output video

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

### VibeVoice API - AI Text to Speech APIs
**Page:** https://www.pixazo.ai/models/vibevoice

> by Microsoft. Convert text into realistic speech with multiple voice options and speaking styles. Supports multi-speaker dialogues and voice cloning.

#### 1. VibeVoice v1 — Text To Speech

**Endpoint:**
```
POST https://gateway.pixazo.ai/vibevoice/v1/vibevoice/generateRequest
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
  "script": "Speaker 0: Hello, this is a test of the VibeVoice API.",
  "speakers": [
    {
      "preset": "Alice [EN]"
    }
  ]
}
```

**Parameters:**

| Parameter | Required | Type | Default | Description |
|-----------|----------|------|---------|-------------|
| script | Yes | string | — | Text to convert. Use `Speaker X:` prefix for multi-speaker dialogues |
| speakers | No | array | [] | Speaker configs. Each has `preset` or `audio_url` |
| speakers[].preset | No | string | Alice [EN] | Voice preset: `Alice [EN]`, `Carter [EN]`, `Frank [EN]`, `Mary [EN]` (Background Music), `Maya [EN]`, `Anchen [ZH]` (Background Music), `Bowen [ZH]`, `Xinran [ZH]` |
| speakers[].audio_url | No | string | — | URL to voice sample for voice cloning (overrides preset) |
| seed | No | integer | — | Random seed for reproducible generation |
| cfg_scale | No | float | 1.3 | Guidance scale (1.0–2.0). Higher = more text-faithful |

**Response:**
```json
{
  "request_id": "vibevoice_019dxxxx-xxxx",
  "status": "QUEUED",
  "polling_url": "https://gateway.pixazo.ai/v2/requests/status/{request_id}"
}
```

**Multi-speaker example:**
```json
{
  "script": "Speaker 0: VibeVoice is now available on Pixazo.\nSpeaker 1: That's right, and it supports up to four speakers!",
  "speakers": [
    { "preset": "Frank [EN]" },
    { "preset": "Carter [EN]" }
  ],
  "cfg_scale": 1.3,
  "seed": 42
}
```

**Pricing:**

| Resolution | Price (USD) |
|------------|-------------|
| 480p | $0.75 |
| 580p | $1.00 |
| 720p | $1.25 |

---

#### 2. VibeVoice v1 — Realtime TTS

**Endpoint:**
```
POST https://gateway.pixazo.ai/vibevoice-realtime-0-5b-135/v1/vibevoice-realtime-0-5b-request
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
  "script": "Speaker 0: Hello, this is Frank.\nSpeaker 1: And I am Carter.",
  "speakers": [
    { "preset": "Frank [EN]" },
    { "preset": "Carter [EN]" }
  ]
}
```

**Parameters:**

| Parameter | Required | Type | Default | Description |
|-----------|----------|------|---------|-------------|
| script | Yes | string | — | Dialogue script with `Speaker X:` labels |
| speakers | Yes | array | — | Array of speaker configs with `preset` field |
| speakers[].preset | Yes | string | — | Voice preset (e.g., "Frank [EN]", "Carter [EN]") |
| cfg_scale | No | number | 1.3 | Guidance scale |

**Response:** Same async pattern — `request_id` + `polling_url`

**Pricing:** Not yet listed

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



## Additional Models


### Category: Image Generation

### Auraflow API - AI 3D Model Generation APIs
**Page:** https://www.pixazo.ai/models/auraflow


by Auraflow

Auraflow API, developers can integrate Auraflow's capabilities to create detailed 3D models, objects, and scenes without manual modeling. The API supports various output formats and provides consistent, production-ready 3D content for games, AR/VR applications, and digital experiences.

Auraflow v0.3
Generate Image


Generate Image

**Request Code**

**Request Parameters**

**Example Request**

**Response**

**Request Headers**

**Response Handling**

#### Auraflow v0.3 Generate Image API Documentation
`https://gateway.pixazo.ai/auraflow-v0-3-512/v1`

**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| --- | --- | --- | --- |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
AuraFlow v0.3 generate request - AuraFlow v0.3

**Request Code**
```http
POST https://gateway.pixazo.ai/auraflow-v0-3-512/v1/auraflow-v0-3-request
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY

{
"prompt": "Close-up portrait of a majestic iguana with vibrant blue-green scales, piercing amber eyes, and orange spiky crest. Intricate textures and details visible on scaly skin. Wrapped in dark hood, giving regal appearance. Dramatic lighting against black background. Hyper-realistic, high-resolution image showcasing the reptiles expressive features and coloration.",
"num_images": 1,
"guidance_scale": 3.5,
"num_inference_steps": 50,
"expand_prompt": true
}
```

**Output**
```json
{
"request_id": "auraflow-v0-3-512_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/auraflow-v0-3-512_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
Webhook (Optional)

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - AuraFlow v0.3 generate request
| Parameter | Required | Type | Description |
| --- | --- | --- | --- |
| prompt | Yes | string | A detailed textual description of the desired image. Be specific about subjects, lighting, style, colors, textures, and composition. |
| num_images | No | integer | Number of images to generate in a single request. |
| guidance_scale | No | number | Controls how closely the generated image adheres to the prompt. Higher values increase prompt fidelity but may reduce creativity. |
| num_inference_steps | No | integer | Number of denoising steps during image generation. Higher values improve detail and quality but increase processing time. |
| expand_prompt | No | boolean | If enabled, the system will automatically enrich your prompt with additional contextually relevant details to enhance image quality. |

**Example Request**
```json
{
"prompt": "Close-up portrait of a majestic iguana with vibrant blue-green scales, piercing amber eyes, and orange spiky crest. Intricate textures and details visible on scaly skin. Wrapped in dark hood, giving regal appearance. Dramatic lighting against black background. Hyper-realistic, high-resolution image showcasing the reptiles expressive features and coloration.",
"num_images": 1,
"guidance_scale": 3.5,
"num_inference_steps": 50,
"expand_prompt": true
}
```

**Response**
```json
{
"request_id": "auraflow-v0-3-512_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/auraflow-v0-3-512_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```

**Request Headers**
| Header | Value |
| --- | --- |
| Content-Type | application/json |
| --- | --- |
| Cache-Control | no-cache |
| Ocp-Apim-Subscription-Key | YOUR_SUBSCRIPTION_KEY |

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
Error Responses

Queue system errors and model validation errors.

Queue System Errors
// 402 — Insufficient balance
```json
{
"error": "Insufficient Balance",
"message": "Your wallet does not have enough balance."
}
```
// 400 — Model not found
```json
{
"error": "Model not found",
"message": "Model 'auraflow-v0-3-512' not found or is disabled"
}
```
Error via Status/Webhook
```json
{
"request_id": "auraflow-v0-3-512_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "ERROR",
"model_id": "auraflow-v0-3-512",
"error": "Description of the error",
"output": null
}
```
Retrieving Results

Poll the universal status endpoint to check progress and retrieve results.

Endpoint
```http
GET https://gateway.pixazo.ai/v2/requests/status/{request_id}
Ocp-Apim-Subscription-Key: YOUR_API_KEY
cURL Example
curl -H "Ocp-Apim-Subscription-Key: YOUR_API_KEY" \
"https://gateway.pixazo.ai/v2/requests/status/auraflow-v0-3-512_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
Response (Completed)
{
"request_id": "auraflow-v0-3-512_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "COMPLETED",
"model_id": "auraflow-v0-3-512",
"error": null,
"output": {
"media_url": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/v1/auraflow-v0-3-512_019dxxxx-xxxx/output.ext"
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
| --- | --- | --- |
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

Auraflow v0.3 Generate Image API Pricing
100% OFF
Limited time discount on API pricing
| Resolution | Price (USD) |
| All Resolution | $0.001 |
API 
---
### Grok Imagine API - AI Image Generation APIs
**Page:** https://www.pixazo.ai/models/grok-imagine


by xAI

Grok Imagine API, developers can access Grok's image generation to create visuals with distinctive artistic qualities. The API provides text-to-image generation with xAI's approach to AI, suitable for creative projects seeking an alternative to mainstream image generators.

Grok v1
Grok Imagine Video t2v
Generate Image


Generate Image

**Request Code**

**Request Parameters**

**Example Request**

**Response**

**Request Headers**

**Response Handling**

#### Grok v1 Generate Image API Documentation
`https://gateway.pixazo.ai/grok-imagine-api-641/v1`

**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| --- | --- | --- | --- |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |

#### Grok Imagine API generate request - Grok Imagine API

**Request Code**
```http
POST https://gateway.pixazo.ai/grok-imagine-api-641/v1/grok-imagine-api-request
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY

{
"prompt": "Abstract human silhouette, golden particles ready to burst outward representing joy, data visualization style, emotional expression through particles, artistic scientific",
"num_images": 1,
"aspect_ratio": "1:1",
"output_format": "jpeg"
}
```

**Output**
```json
{
"request_id": "grok-imagine-api-641_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/grok-imagine-api-641_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
Webhook (Optional)

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Grok Imagine API generate request
| Parameter | Required | Type | Description |
| --- | --- | --- | --- |
| prompt | Yes | string | A detailed text description of the desired image or animation. Include style, mood, composition, and visual elements for optimal results. |
| num_images | No | integer | The number of images to generate in a single request. Supports batch generation. |
| aspect_ratio | No | string | The aspect ratio of the generated image. Common values include "1:1", "16:9", "9:16", "4:3", "3:2". |
| output_format | No | string | The file format of the output image. Supported values: "jpeg", "png", "webp". |

**Example Request**
```json
{
"prompt": "Abstract human silhouette, golden particles ready to burst outward representing joy, data visualization style, emotional expression through particles, artistic scientific",
"num_images": 1,
"aspect_ratio": "1:1",
"output_format": "jpeg"
}
```

**Response**
```json
{
"request_id": "grok-imagine-api-641_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/grok-imagine-api-641_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```

**Request Headers**
| Header | Value |
| --- | --- |
| Content-Type | application/json |
| --- | --- |
| Cache-Control | no-cache |
| Ocp-Apim-Subscription-Key | YOUR_SUBSCRIPTION_KEY |

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
Error Responses

Queue system errors and model validation errors.

Queue System Errors
// 402 — Insufficient balance
```json
{
"error": "Insufficient Balance",
"message": "Your wallet does not have enough balance."
}
```
// 400 — Model not found
```json
{
"error": "Model not found",
"message": "Model 'grok-imagine-api-641' not found or is disabled"
}
```
Error via Status/Webhook
```json
{
"request_id": "grok-imagine-api-641_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "ERROR",
"model_id": "grok-imagine-api-641",
"error": "Description of the error",
"output": null
}
```
Retrieving Results

Poll the universal status endpoint to check progress and retrieve results.

Endpoint
```http
GET https://gateway.pixazo.ai/v2/requests/status/{request_id}
Ocp-Apim-Subscription-Key: YOUR_API_KEY
cURL Example
curl -H "Ocp-Apim-Subscription-Key: YOUR_API_KEY" \
"https://gateway.pixazo.ai/v2/requests/status/grok-imagine-api-641_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
Response (Completed)
{
"request_id": "grok-imagine-api-641_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "COMPLETED",
"model_id": "grok-imagine-api-641",
"error": null,
"output": {
"media_url": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/v1/grok-imagine-api-641_019dxxxx-xxxx/output.ext"
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
| --- | --- | --- |
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

Grok v1 Generate Image API Pricing
| Resolution | Price (USD) |
| All Resolution | $0.018 |
2. Grok Imagine Video t2v

#### Grok Imagine Video t2v Text To Video API Documentation
`https://gateway.pixazo.ai/grok-imagine-video/v1`

**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| --- | --- | --- | --- |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Grok Imagine Video generate request - Grok Imagine Video

**Request Code**
```http
POST https://gateway.pixazo.ai/grok-imagine-video/v1/grok-imagine-video-request
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY

{
"prompt": "A golden sunset timelapse over a coastal city, waves crashing against the pier, seagulls flying, warm cinematic lighting, aerial drone perspective",
"duration": 6,
"aspect_ratio": "16:9",
"resolution": "720p"
}
```

**Output**
```json
{
"request_id": "grok-imagine-video_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/grok-imagine-video_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
Webhook (Optional)

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Grok Imagine Video generate request
| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| prompt | string | Yes | — | A detailed text description of the desired video scene, including motion, lighting, perspective, and mood |
| duration | integer | Yes | — | Length of the generated video in seconds (minimum 3, maximum 10) |
| aspect_ratio | string | Yes | — | Video aspect ratio; supported values: "16:9", "9:16", "1:1" |
| resolution | string | Yes | — | Output video resolution; supported values: "720p", "1080p" |
Minimum Request
```json
{
"prompt": "A golden sunset timelapse over a coastal city, waves crashing against the pier, seagulls flying, warm cinematic lighting, aerial drone perspective",
"duration": 6,
"aspect_ratio": "16:9",
"resolution": "720p"
}
```
Full Request (all options)
```json
{
"prompt": "A golden sunset timelapse over a coastal city, waves crashing against the pier, seagulls flying, warm cinematic lighting, aerial drone perspective",
"duration": 6,
"aspect_ratio": "16:9",
"resolution": "720p"
}
```

**Response**
```json
{
"request_id": "grok-imagine-video_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/grok-imagine-video_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```

**Request Headers**
| Header | Value |
| --- | --- |
| Content-Type | application/json |
| --- | --- |
| Cache-Control | no-cache |
| Ocp-Apim-Subscription-Key | Your API subscription key |

**Response Handling**

Common status codes for Grok Imagine Video generate request.

| Code | Meaning |
| 202 | Accepted — Request queued |
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
Error Responses

Queue system errors and model validation errors.

Queue System Errors
// 402 — Insufficient balance
```json
{
"error": "Insufficient Balance",
"message": "Your wallet does not have enough balance."
}
```
// 400 — Model not found
```json
{
"error": "Model not found",
"message": "Model 'grok-imagine-video' not found or is disabled"
}
```
Error via Status/Webhook
```json
{
"request_id": "grok-imagine-video_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "ERROR",
"model_id": "grok-imagine-video",
"error": "Description of the error",
"output": null
}
```
Retrieving Results

Poll the universal status endpoint to check progress and retrieve results.

Endpoint
```http
GET https://gateway.pixazo.ai/v2/requests/status/{request_id}
Ocp-Apim-Subscription-Key: YOUR_API_KEY
cURL Example
curl -H "Ocp-Apim-Subscription-Key: YOUR_API_KEY" \
"https://gateway.pixazo.ai/v2/requests/status/grok-imagine-video_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
Response (Completed)
{
"request_id": "grok-imagine-video_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "COMPLETED",
"model_id": "grok-imagine-video",
"error": null,
"output": {
"media_url": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/v1/grok-imagine-video_019dxxxx-xxxx/output.ext"
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
| --- | --- | --- |
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

Grok Imagine Video t2v Text To Video API Pricing
| Resolution | Price (USD) |
| 480p | $0.05 per_second |
| 720p | $0.07 per_second |
API 
---
### Kandinsky 5 Pro API - AI Video Generation APIs
**Page:** https://www.pixazo.ai/models/kandinsky


by Kandinsky

Kandinsky 5 Pro API, developers can leverage Kandinsky's image-to-video capabilities to bring still images to life with realistic movement and dynamic visual storytelling. The API is designed for content creators, marketers, and developers who need reliable AI video generation with professional-grade output.

Kandinsky v5 Pro
Image To Video


Image To Video

**Request Code**

**Request Parameters**

**Example Request**

**Response**

**Request Headers**

**Response Handling**

#### Kandinsky v5 Pro Image To Video API Documentation
`https://gateway.pixazo.ai/kandinsky-5-0-pro-953/v1`

**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| --- | --- | --- | --- |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |

#### Kandinsky 5.0 Pro generate request - Kandinsky 5.0 Pro API

**Request Code**
```http
POST https://gateway.pixazo.ai/kandinsky-5-0-pro-953/v1/kandinsky-5-0-pro-request
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY

{
"prompt": "The white dragon warrior stands still, eyes full of determination and strength. The camera slowly moves closer or circles around the warrior, highlighting the powerful presence and heroic spirit of the character.",
"image_url": "https://storage.googleapis.com/falserverless/model_tests/wan/dragon-warrior.jpg"
}
```

**Output**
```json
{
"request_id": "kandinsky-5-0-pro-953_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/kandinsky-5-0-pro-953_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
Webhook (Optional)

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Kandinsky 5.0 Pro generate request
| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| prompt | string | Yes | — | A detailed textual description of the desired motion, camera movement, and emotional tone for the generated video. |
| image_url | string | Yes | — | Publicly accessible URL of the input image to animate. Must be reachable by the server. |
| resolution | string | No | 512P | Output video resolution preset. Choose from "256P", "512P", or "1024P". Higher resolutions yield more detail but take longer to generate. |
| duration | string | No | 5s | Total duration of the generated video. Accepts values like "3s", "5s", "10s". |
| num_inference_steps | integer | No | 28 | Number of denoising steps during video generation. Higher values improve quality at the cost of longer processing. Range: 20–50. |
| acceleration | string | No | regular | Optimization setting for generation speed vs. quality. Options: "regular", "fast", "ultra_fast". |
Minimum Request
```json
{
"prompt": "The white dragon warrior stands still, eyes full of determination and strength. The camera slowly moves closer or circles around the warrior, highlighting the powerful presence and heroic spirit of the character.",
"image_url": "https://storage.googleapis.com/falserverless/model_tests/wan/dragon-warrior.jpg"
}
```
Full Request (all options)
```json
{
"prompt": "The white dragon warrior stands still, eyes full of determination and strength. The camera slowly moves closer or circles around the warrior, highlighting the powerful presence and heroic spirit of the character.",
"image_url": "https://storage.googleapis.com/falserverless/model_tests/wan/dragon-warrior.jpg",
"resolution": "512P",
"duration": "5s",
"num_inference_steps": 28,
"acceleration": "regular"
}
```

**Response**
```json
{
"request_id": "kandinsky-5-0-pro-953_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/kandinsky-5-0-pro-953_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```

**Request Headers**
| Header | Value |
| --- | --- |
| Content-Type | application/json |
| --- | --- |
| Cache-Control | no-cache |
| Ocp-Apim-Subscription-Key | Your API subscription key |

**Response Handling**

Common status codes for Kandinsky 5.0 Pro generate request.

| Code | Meaning |
| 202 | Accepted — Request queued |
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
Error Responses

Queue system errors and model validation errors.

Queue System Errors
// 402 — Insufficient balance
```json
{
"error": "Insufficient Balance",
"message": "Your wallet does not have enough balance."
}
```
// 400 — Model not found
```json
{
"error": "Model not found",
"message": "Model 'kandinsky-5-0-pro-953' not found or is disabled"
}
```
Error via Status/Webhook
```json
{
"request_id": "kandinsky-5-0-pro-953_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "ERROR",
"model_id": "kandinsky-5-0-pro-953",
"error": "Description of the error",
"output": null
}
```
Retrieving Results

Poll the universal status endpoint to check progress and retrieve results.

Endpoint
```http
GET https://gateway.pixazo.ai/v2/requests/status/{request_id}
Ocp-Apim-Subscription-Key: YOUR_API_KEY
cURL Example
curl -H "Ocp-Apim-Subscription-Key: YOUR_API_KEY" \
"https://gateway.pixazo.ai/v2/requests/status/kandinsky-5-0-pro-953_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
Response (Completed)
{
"request_id": "kandinsky-5-0-pro-953_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "COMPLETED",
"model_id": "kandinsky-5-0-pro-953",
"error": null,
"output": {
"media_url": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/v1/kandinsky-5-0-pro-953_019dxxxx-xxxx/output.ext"
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
| --- | --- | --- |
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

Kandinsky v5 Pro Image To Video API Pricing
| Resolution | Price (USD) |
| 512P | $0.04 per second |
| 1024P | $0.12 per second |
API 
---
### LongCat Image API - AI Image Generation APIs
**Page:** https://www.pixazo.ai/models/longcat-image


by LongCat

LongCat Image API, developers can generate detailed images for various applications including illustrations, concept art, and marketing visuals. The API offers reliable performance and supports standard image generation workflows with customizable parameters.

LongCat v1
Text To Image


Text To Image

**Request Code**

**Request Parameters**

**Example Request**

**Response**

**Request Headers**

**Response Handling**

#### LongCat v1 Text To Image API Documentation
`https://gateway.pixazo.ai/longcat-image-498/v1`

**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| --- | --- | --- | --- |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
LongCat-Image generate request - LongCat-Image

**Request Code**
```http
POST https://gateway.pixazo.ai/longcat-image-498/v1/longcat-image-request
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY

{
"prompt": "A lioness crouching in the tall dry grass of the Serengeti during golden hour, intense gaze, telephoto lens with shallow depth of field",
"image_size": "landscape_4_3",
"num_inference_steps": 28,
"guidance_scale": 4.5,
"num_images": 1,
"enable_safety_checker": true,
"output_format": "png",
"acceleration": "regular"
}
```

**Output**
```json
{
"request_id": "longcat-image-498_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/longcat-image-498_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
Webhook (Optional)

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - LongCat-Image generate request
| Parameter | Required | Type | Description |
| --- | --- | --- | --- |
| prompt | Yes | string | Detailed textual description of the desired image content. Be specific for best results. |
| image_size | Yes | string | Aspect ratio and resolution preset. Supported values: `landscape_4_3`, `portrait_3_4`, `square_1_1`, `ultra_wide_16_9`, `ultra_portrait_9_16`. |
| num_inference_steps | No | integer | Number of denoising steps during image generation. Higher values increase quality but slow generation. |
| guidance_scale | No | number | Controls how closely the generated image follows the prompt. Higher values increase prompt fidelity but may reduce diversity. |
| num_images | No | integer | Number of images to generate per request. |
| enable_safety_checker | No | boolean | Enables content safety filtering to block inappropriate outputs. |
| output_format | No | string | File format of generated images. Supported values: `png`, `jpeg`, `webp`. |
| acceleration | No | string | Rendering optimization mode. Values: `regular`, `high_performance`. Use `high_performance` for faster generation with potential quality trade-offs. |

**Example Request**
```json
{
"prompt": "A lioness crouching in the tall dry grass of the Serengeti during golden hour, intense gaze, telephoto lens with shallow depth of field",
"image_size": "landscape_4_3",
"num_inference_steps": 28,
"guidance_scale": 4.5,
"num_images": 1,
"enable_safety_checker": true,
"output_format": "png",
"acceleration": "regular"
}
```

**Response**
```json
{
"request_id": "longcat-image-498_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/longcat-image-498_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```

**Request Headers**
| Header | Value |
| --- | --- |
| Content-Type | application/json |
| --- | --- |
| Cache-Control | no-cache |
| Ocp-Apim-Subscription-Key | YOUR_SUBSCRIPTION_KEY |

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
Error Responses

Queue system errors and model validation errors.

Queue System Errors
// 402 — Insufficient balance
```json
{
"error": "Insufficient Balance",
"message": "Your wallet does not have enough balance."
}
```
// 400 — Model not found
```json
{
"error": "Model not found",
"message": "Model 'longcat-image-498' not found or is disabled"
}
```
Error via Status/Webhook
```json
{
"request_id": "longcat-image-498_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "ERROR",
"model_id": "longcat-image-498",
"error": "Description of the error",
"output": null
}
```
Retrieving Results

Poll the universal status endpoint to check progress and retrieve results.

Endpoint
```http
GET https://gateway.pixazo.ai/v2/requests/status/{request_id}
Ocp-Apim-Subscription-Key: YOUR_API_KEY
cURL Example
curl -H "Ocp-Apim-Subscription-Key: YOUR_API_KEY" \
"https://gateway.pixazo.ai/v2/requests/status/longcat-image-498_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
Response (Completed)
{
"request_id": "longcat-image-498_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "COMPLETED",
"model_id": "longcat-image-498",
"error": null,
"output": {
"media_url": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/v1/longcat-image-498_019dxxxx-xxxx/output.ext"
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
| --- | --- | --- |
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

LongCat v1 Text To Image API Pricing
| Resolution | Price (USD) |
| Per Generation | $0.13 |
API 
---
### Pixelforge API - AI Image Generation & Relighting APIs
**Page:** https://www.pixazo.ai/models/pixelforge


by Pixazo

Pixelforge API, developers can generate new images and transform the lighting of existing photos for product photography, real estate, and creative applications. The API's relighting feature is particularly valuable for e-commerce and professional photography workflows.

PixelForge v1
Generate Image
Image Relighting


Generate Image

**Request Code**

**Request Parameters**

**Example Request**

**Response**

**Request Headers**

**Response Handling**
Image Relighting

#### PixelForge v1 Generate Image API Documentation

**Request Code**
```http
POST https://gateway.pixazo.ai/pixelforge-image/v1/qwen_image_gen/serve_image
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: your-subscription-key-here

{
"prompt": "A futuristic city skyline at sunset with flying cars and neon signs",
"image_urls": [
"https://example.com/reference-image.jpg"
]
}
```

**Output**
```json
{
"url": "https://cdn.pixazo.ai/images/abc123.jpg"
}
```
Request Parameters - Generate Image
| Parameter | Required | Type | Description |
| --- | --- | --- | --- |
| prompt | Yes | string | The text prompt describing the image you want to generate. You can provide detailed and creative descriptions to guide the image generation process. |
| image_urls | Yes | array | Minimum should be one image, maximum three |

**Example Request**
```json
{
"prompt": "A futuristic city skyline at sunset with flying cars and neon signs",
"image_urls": [
"https://example.com/reference-image.jpg"
]
}
```

**Response**
```json
{
"url": "https://cdn.pixazo.ai/images/abc123.jpg"
}
```

**Request Headers**
| Header | Value |
| --- | --- |
| Content-Type | application/json |
| --- | --- |
| Cache-Control | no-cache |
| Ocp-Apim-Subscription-Key | Your API subscription key for authentication |

**Response Handling**

Common status codes for Generate Image.

| Code | Meaning |
| 200 | Success |
| 400 | Bad Request |
| 401 | Unauthorized |
| 403 | Forbidden |
| 404 | Not Found |
| 429 | Too Many Requests |
| 500 | Internal Server Error |
PixelForge v1 Generate Image API Pricing

No data available


#### PixelForge v1 Image Relighting API Documentation
`https://gateway.pixazo.ai/pixelforge-relighting-api/v1`

**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| --- | --- | --- | --- |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |

#### Image Edit Request - Pixelforge Relighting API

**Request Code**
```http
POST https://gateway.pixazo.ai/pixelforge-relighting-api/v1/relighting/generate
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY

{
"prompt": "Professional studio lighting with soft shadows",
"image_urls": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/f1.png"
]
}
```

**Output**
```json
{
"request_id": "pixelforge-relighting-api_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/pixelforge-relighting-api_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
Webhook (Optional)

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Image Edit Request
| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| prompt | string | Yes | — | Text description of desired lighting effect. Max 800 characters. |
| image_urls | array | Yes | — | Array of reference image URLs (1-10 images). Must be publicly accessible. Supports JPEG, PNG, WEBP. |
| image_size | string/object | No | "square_hd" | Output size. Options: "square_hd", "square", "portrait_4_3", "portrait_16_9", "landscape_4_3", "landscape_16_9" or custom {"width": 1280, "height": 720} |
| num_inference_steps | integer | No | 50 | Number of inference steps. Range: 1-150. Higher = better quality but slower. |
| guidance_scale | number | No | 4 | How closely to follow the prompt. Range: 1-20. |
| num_images | integer | No | 1 | Number of images to generate. Range: 1-4. |
| enable_safety_checker | boolean | No | true | Enable content safety filtering. |
| output_format | string | No | "png" | Output format: "jpeg" or "png". |
| negative_prompt | string | No | " " | Elements to exclude from generation. |
| acceleration | string | No | "regular" | Speed optimization: "none" or "regular". |
| loras | array | No | [] | Array of LoRA weights to apply (max 3). Each object has "path" (string, required) and "scale" (number, 0.0-2.0, default 1.0). |
| webhook | string | No | — | Webhook URL for async notifications. |
| webhook_events_filter | array | No | ["*"] | Event types: ["start"], ["complete"], ["*"]. |
Minimum Request
```json
{
"prompt": "Professional lighting",
"image_urls": [
"https://example.com/photo.jpg"
]
}
```
Full Request (all options)
```json
{
"prompt": "Professional studio lighting with soft shadows",
"image_urls": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/f1.png"
],
"image_size": "square_hd",
"num_inference_steps": 50,
"guidance_scale": 4,
"num_images": 1,
"enable_safety_checker": true,
"output_format": "png",
"negative_prompt": " ",
"acceleration": "regular",
"loras": [
{
"path": "https://.../files/lighting-lora.safetensors",
"scale": 1.0
}
```
],
"webhook": "https://your-domain.com/webhook",
"webhook_events_filter": [
"*"
]
}

**Response**
```json
{
"request_id": "pixelforge-relighting-api_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/pixelforge-relighting-api_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```

**Request Headers**
| Header | Value |
| --- | --- |
| Content-Type | application/json |
| --- | --- |
| Cache-Control | no-cache |
| Ocp-Apim-Subscription-Key | Your API subscription key |

**Response Handling**

Common status codes for Image Edit Request.

| Code | Meaning |
| 202 | Accepted — Request queued |
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
Error Responses

Queue system errors and model validation errors.

Queue System Errors
// 402 — Insufficient balance
```json
{
"error": "Insufficient Balance",
"message": "Your wallet does not have enough balance."
}
```
// 400 — Model not found
```json
{
"error": "Model not found",
"message": "Model 'pixelforge-relighting-api' not found or is disabled"
}
```
Error via Status/Webhook
```json
{
"request_id": "pixelforge-relighting-api_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "ERROR",
"model_id": "pixelforge-relighting-api",
"error": "Description of the error",
"output": null
}
```
Retrieving Results

Poll the universal status endpoint to check progress and retrieve results.

Endpoint
```http
GET https://gateway.pixazo.ai/v2/requests/status/{request_id}
Ocp-Apim-Subscription-Key: YOUR_API_KEY
cURL Example
curl -H "Ocp-Apim-Subscription-Key: YOUR_API_KEY" \
"https://gateway.pixazo.ai/v2/requests/status/pixelforge-relighting-api_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
Response (Completed)
{
"request_id": "pixelforge-relighting-api_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "COMPLETED",
"model_id": "pixelforge-relighting-api",
"error": null,
"output": {
"media_url": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/v1/pixelforge-relighting-api_019dxxxx-xxxx/output.ext"
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
| --- | --- | --- |
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

PixelForge v1 Image Relighting API Pricing
| Resolution | Price (USD) |
| All Resolution | $0.04 |
API 
---
### Qwen Image 2 Pro API, Qwen Image Edit API, Qwen Image API - AI Image Generation & Editing APIs
**Page:** https://www.pixazo.ai/models/qwen-image


by Alibaba

Qwen Image 2 Pro API, developers can access text-to-image generation, image editing, layered image creation, and LoRA training features. The API represents Alibaba's advanced AI research applied to visual content creation, suitable for both consumer applications and enterprise workflows.

Qwen Image Max Edit
Qwen Image Max t2i
Qwen Image Edit
Qwen Image
Qwen LoRA v1
Image Max Edit


Image Max Edit

**Request Code**

**Request Parameters**

**Example Request**

**Response**

**Request Headers**

**Response Handling**

#### Qwen Image Max Edit Image Max Edit API Documentation
`https://gateway.pixazo.ai/qwen-image-max-edit/v1`

**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| --- | --- | --- | --- |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Qwen Image Max Edit generate request - Qwen Image Max Edit

**Request Code**
```http
POST https://gateway.pixazo.ai/qwen-image-max-edit/v1/qwen-image-max-edit-request
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY

{
"prompt": "Transform the background into a serene mountain landscape with snow-capped peaks and a clear blue sky",
"image_urls": [
"https://imagesai.appypie.com/7686410/JUEOHp2Y3FDjmXwOQJVy_017731476841749.png"
]
}
```

**Output**
```json
{
"request_id": "qwen-image-max-edit_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/qwen-image-max-edit_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
Webhook (Optional)

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Qwen Image Max Edit generate request
| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| prompt | string | Yes | — | Detailed text description of the desired image edit. Specifies what changes to apply to the input image. |
| negative_prompt | string | No | — | Describes unwanted elements or artifacts to avoid in the output. Improves output quality by exclusion. |
| enable_prompt_expansion | boolean | No | true | Enables AI-driven expansion of the prompt for richer, more detailed interpretations. |
| enable_safety_checker | boolean | No | true | Activates content safety filtering to block inappropriate or harmful outputs. |
| num_images | integer | No | 1 | Number of edited images to generate. Must be between 1 and 4. |
| output_format | string | No | png | Output image format. Supported values: png, jpeg, webp. |
| image_urls | array of strings | Yes | — | Array of one or more public HTTP URLs pointing to the source images to be edited. Only the first URL is processed if multiple are provided. |
Minimum Request
```json
{
"prompt": "Transform the background into a serene mountain landscape with snow-capped peaks and a clear blue sky",
"image_urls": [
"https://imagesai.appypie.com/7686410/JUEOHp2Y3FDjmXwOQJVy_017731476841749.png"
]
}
```
Full Request (all options)
```json
{
"prompt": "Transform the background into a serene mountain landscape with snow-capped peaks and a clear blue sky",
"negative_prompt": "low resolution, error, worst quality, low quality, deformed",
"enable_prompt_expansion": true,
"enable_safety_checker": true,
"num_images": 1,
"output_format": "png",
"image_urls": [
"https://imagesai.appypie.com/7686410/JUEOHp2Y3FDjmXwOQJVy_017731476841749.png"
]
}
```

**Response**
```json
{
"request_id": "qwen-image-max-edit_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/qwen-image-max-edit_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```

**Request Headers**
| Header | Value |
| --- | --- |
| Content-Type | application/json |
| --- | --- |
| Cache-Control | no-cache |
| Ocp-Apim-Subscription-Key | Your API subscription key |

**Response Handling**

Common status codes for Qwen Image Max Edit generate request.

| Code | Meaning |
| 202 | Accepted — Request queued |
| 400 | Bad Request |
| 401 | Unauthorized |
| 403 | Forbidden |
| 404 | Not Found |
| 429 | Too Many Requests |
| 500 | Internal Server Error |
Notes & Tips
Poll the results endpoint every 2–3 seconds until status is COMPLETED or FAILED
Implement exponential backoff for retry logic on 500 or 429 responses
Use clear, specific prompts that describe the desired change in context, lighting, or environment
Avoid overly vague prompts like “make it better”; specify elements such as “replace sky with sunset”
Validate image URLs are publicly accessible and use HTTPS before submission
Limit num_images to 1 unless multiple variations are required to

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
Error Responses

Queue system errors and model validation errors.

Queue System Errors
// 402 — Insufficient balance
```json
{
"error": "Insufficient Balance",
"message": "Your wallet does not have enough balance."
}
```
// 400 — Model not found
```json
{
"error": "Model not found",
"message": "Model 'qwen-image-max-edit' not found or is disabled"
}
```
Error via Status/Webhook
```json
{
"request_id": "qwen-image-max-edit_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "ERROR",
"model_id": "qwen-image-max-edit",
"error": "Description of the error",
"output": null
}
```
Retrieving Results

Poll the universal status endpoint to check progress and retrieve results.

Endpoint
```http
GET https://gateway.pixazo.ai/v2/requests/status/{request_id}
Ocp-Apim-Subscription-Key: YOUR_API_KEY
cURL Example
curl -H "Ocp-Apim-Subscription-Key: YOUR_API_KEY" \
"https://gateway.pixazo.ai/v2/requests/status/qwen-image-max-edit_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
Response (Completed)
{
"request_id": "qwen-image-max-edit_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "COMPLETED",
"model_id": "qwen-image-max-edit",
"error": null,
"output": {
"media_url": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/v1/qwen-image-max-edit_019dxxxx-xxxx/output.ext"
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
| --- | --- | --- |
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

Qwen Image Max Edit Image Max Edit API Pricing

No data available

2. Qwen Image Max t2i

#### Qwen Image Max t2i Image Max T2I API Documentation
`https://gateway.pixazo.ai/qwen-image-max/v1`

**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| --- | --- | --- | --- |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Qwen Image Max generate request - Qwen Image Max

**Request Code**
```http
POST https://gateway.pixazo.ai/qwen-image-max/v1/qwen-image-max-request
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY

{
"prompt": "A majestic white tiger resting on a mossy rock beside a waterfall in a tropical rainforest, photorealistic"
}
```

**Output**
```json
{
"request_id": "qwen-image-max_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/qwen-image-max_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
Webhook (Optional)

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Qwen Image Max generate request
| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| prompt | string | Yes | — | A detailed text description of the desired image. Be specific about subjects, styles, lighting, and composition. |
| negative_prompt | string | No | — | A description of elements to avoid in the generated image. Helps refine output quality by excluding unwanted features. |
| image_size | string | No | square_hd | The resolution and aspect ratio of the output image. Supported values: square_hd, portrait_hd, landscape_hd. |
| enable_prompt_expansion | boolean | No | true | Enables AI-driven enhancement of the prompt for richer, more detailed generation. |
| enable_safety_checker | boolean | No | true | Activates content filtering to block inappropriate or harmful outputs. |
| num_images | integer | No | 1 | Number of images to generate in a single request. Maximum value is 4. |
| output_format | string | No | png | The file format of the generated image. Supported values: png, jpeg, webp. |
Minimum Request
```json
{
"prompt": "A majestic white tiger resting on a mossy rock beside a waterfall in a tropical rainforest, photorealistic"
}
```
Full Request (all options)
```json
{
"prompt": "A majestic white tiger resting on a mossy rock beside a waterfall in a tropical rainforest, photorealistic",
"negative_prompt": "low resolution, error, worst quality, low quality, deformed",
"image_size": "square_hd",
"enable_prompt_expansion": true,
"enable_safety_checker": true,
"num_images": 1,
"output_format": "png"
}
```

**Response**
```json
{
"request_id": "qwen-image-max_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/qwen-image-max_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```

**Request Headers**
| Header | Value |
| --- | --- |
| Content-Type | application/json |
| --- | --- |
| Cache-Control | no-cache |
| Ocp-Apim-Subscription-Key | Your API subscription key |

**Response Handling**

Common status codes for Qwen Image Max generate request.

| Code | Meaning |
| 202 | Accepted — Request queued |
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
Error Responses

Queue system errors and model validation errors.

Queue System Errors
// 402 — Insufficient balance
```json
{
"error": "Insufficient Balance",
"message": "Your wallet does not have enough balance."
}
```
// 400 — Model not found
```json
{
"error": "Model not found",
"message": "Model 'qwen-image-max' not found or is disabled"
}
```
Error via Status/Webhook
```json
{
"request_id": "qwen-image-max_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "ERROR",
"model_id": "qwen-image-max",
"error": "Description of the error",
"output": null
}
```
Retrieving Results

Poll the universal status endpoint to check progress and retrieve results.

Endpoint
```http
GET https://gateway.pixazo.ai/v2/requests/status/{request_id}
Ocp-Apim-Subscription-Key: YOUR_API_KEY
cURL Example
curl -H "Ocp-Apim-Subscription-Key: YOUR_API_KEY" \
"https://gateway.pixazo.ai/v2/requests/status/qwen-image-max_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
Response (Completed)
{
"request_id": "qwen-image-max_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "COMPLETED",
"model_id": "qwen-image-max",
"error": null,
"output": {
"media_url": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/v1/qwen-image-max_019dxxxx-xxxx/output.ext"
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
| --- | --- | --- |
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

Qwen Image Max t2i Image Max T2I API Pricing

No data available

3. Qwen Image Edit

#### Qwen Image Edit Image Edit API Documentation

**Request Code**
```http
POST https://gateway.pixazo.ai/qwen-image/v1/generateMultimodeTextToImageEditRequest
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY

{
"model": "qwen-image-edit",
"input": {
"messages": [
{
"role": "user",
"content": [
{
"image": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/manwithbear.jpg"
},
{
"text": "Change the person to a walking position, bending over to hold the bears front paws."
}
```
]
}
]
},
"parameters": {
"negative_prompt": "",
"watermark": false
}
}

**Output**
```json
{
"status_code": 200,
"request_id": "3daccb10-10ca-9399-8b6a-xxxxxx",
"output": {
"choices": [
{
"message": {
"content": [
{
"image": "https://pub-...png"
}
```
]
}
}
]
}
}
Request Parameters - Image Edit(Img2Img)
| Parameter | Required | Type | Description |
| --- | --- | --- | --- |
| model | Yes | string | Model to use. Available value: "qwen-image-edit" (Qwen image editing model). |
| input.messages | Yes | array | Array of message objects containing the image editing request. Must contain at least one user message. |
| input.messages[].role | Yes | string | Role of the message sender. Must be "user" for image editing requests. |
| input.messages[].content | Yes | array | Array of content objects containing both the input image and editing instructions. |
| input.messages[].content[].image | Yes | string | Input image for editing. Can be a publicly accessible HTTP/HTTPS URL or Base64-encoded image data in format data:{MIME_type};base64,{base64_data}. |
| input.messages[].content[].text | Yes | string | Text instructions describing the desired edits. Supports complex editing tasks including text editing, color adjustment, style transfer, and object manipulation. |
| parameters.negative_prompt | No | string | Negative prompt to specify what should not appear in the edited image. Default: "" (empty string). |
| parameters.watermark | No | boolean | Whether to add a watermark to the edited image. Default: false. |

**Example Request**
```json
{
"model": "qwen-image-edit",
"input": {
"messages": [
{
"role": "user",
"content": [
{
"image": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/manwithbear.jpg"
},
{
"text": "Change the person to a walking position, bending over to hold the bear's front paws."
}
```
]
}
]
},
"parameters": {
"negative_prompt": "",
"watermark": false
}
}

**Response**
```json
{
"status_code": 200,
"request_id": "3daccb10-10ca-9399-8b6a-xxxxxx",
"code": "",
"message": "",
"output": {
"text": null,
"finish_reason": null,
"choices": [
{
"finish_reason": "stop",
"message": {
"role": "assistant",
"content": [
{
"image": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/qwen-image-edit/qwen-image-edit-3daccb10-10ca-9399-8b6a-xxxxxx-1703123456789.png"
}
```
]
}
}
]
},
"usage": {
"input_tokens": 0,
"output_tokens": 0,
"width": 1248,
"image_count": 1,
"height": 832
}
}

**Request Headers**
| Header | Value |
| --- | --- |
| Content-Type | application/json |
| --- | --- |
| Cache-Control | no-cache |
| Ocp-Apim-Subscription-Key | YOUR_SUBSCRIPTION_KEY |

**Response Handling**

Common status codes for Image Edit(Img2Img).

| Code | Meaning |
| 200 | Success |
| 400 | Bad Request |
| 401 | Unauthorized |
| 403 | Forbidden |
| 404 | Not Found |
| 429 | Too Many Requests |
| 500 | Internal Server Error |
Qwen Image Edit Image Edit API Pricing
| Resolution | Price (USD) |
| All Resolution | $0.045 |
4. Qwen Image

#### Qwen Image Text To Image API Documentation
`https://gateway.pixazo.ai/qwen-image/v1`

**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| --- | --- | --- | --- |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |

#### Text To Image Request - Qwen Image API

**Request Code**
POST /generateMultimodeTextToImageRequest HTTP/1.1
Host: gateway.pixazo.ai
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY

```json
{
"model": "qwen-image",
"input": {
"messages": [
{
"role": "user",
"content": [
{
"text": "A serene lake at sunset, with mountains reflected in the water and a lone canoe on the shore."
}
```
]
}
]
},
"parameters": {
"size": "1328*1328"
}
}

**Output**
```json
{
"images": [
{
"file_name": "nano-banana-pro-edit-output.png",
"content_type": "image/png",
"url": "[RESPONSE_URL]"
}
```
],
"description": ""
}
Request Parameters - Text To Image Request
| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| model | string | Yes | — | Model to use. Available value: "qwen-image" (Qwen text-to-image generation model). |
| input.messages | array | Yes | — | Array of message objects containing the generation request. Must contain at least one user message. |
| input.messages[].role | string | Yes | — | Role of the message sender. Must be "user" for text-to-image generation requests. |
| input.messages[].content | array | Yes | — | Array of content objects containing the text prompt for image generation. |
| input.messages[].content[].text | string | Yes | — | Text prompt describing the image to generate. Supports complex descriptions, multi-line layouts, and fine-grained details. Excels at Chinese and English text rendering. |
| parameters.negative_prompt | string | No | "" | Negative prompt to specify what should not appear in the generated image. |
| parameters.prompt_extend | boolean | No | true | Whether to extend and enhance the input prompt automatically. |
| parameters.watermark | boolean | No | true | Whether to add a watermark to the generated image. |
| parameters.size | string | No | 1328*1328 | Output image dimensions in format "WIDTHxHEIGHT". Available sizes: "1328*1328", "1024*1024", "768*768", "512*512". |
Minimum Request
```json
{
"model": "qwen-image",
"input": {
"messages": [
{
"role": "user",
"content": [
{
"text": "A serene lake at sunset, with mountains reflected in the water and a lone canoe on the shore."
}
```
]
}
]
},
"parameters": {
"size": "1328*1328"
}
}
Full Request (all options)
```json
{
"model": "qwen-image",
"input": {
"messages": [
{
"role": "user",
"content": [
{
"text": "A serene lake at sunset, with mountains reflected in the water and a lone canoe on the shore."
}
```
]
}
]
},
"parameters": {
"negative_prompt": "",
"prompt_extend": true,
"watermark": true,
"size": "1328*1328"
}
}

**Response**
```json
{
"images": [
{
"file_name": "nano-banana-pro-edit-output.png",
"content_type": "image/png",
"url": "[RESPONSE_URL]"
}
```
],
"description": ""
}
Response Fields - Text To Image Request
| Field | Type | Description |
| --- | --- | --- |
| images | array | Array of generated image objects. |
| images[].file_name | string | Name of the generated image file. |
| images[].content_type | string | MIME type of the image, typically "image/png". |
| images[].url | string | URL where the generated image can be downloaded. |
| description | string | Optional descriptive text about the generated image, currently always empty. |

**Request Headers**
| Header | Value |
| --- | --- |
| Content-Type | application/json |
| --- | --- |
| Cache-Control | no-cache |
| Ocp-Apim-Subscription-Key | Your API subscription key |

**Response Handling**

Common status codes for Text To Image Request.

| Code | Meaning |
| 200 | Success |
| 400 | Bad Request |
| 401 | Unauthorized |
| 403 | Forbidden |
| 404 | Not Found |
| 429 | Too Many Requests |
| 500 | Internal Server Error |
Qwen Image Text To Image API Pricing
| Resolution | Price (USD) |
| All Resolution | $0.045 |
5. Qwen LoRA v1

#### Qwen LoRA v1 Generate (LoRA) API Documentation
`https://gateway.pixazo.ai/qwen-image-edit-plus/v1`

**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| --- | --- | --- | --- |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |

#### Generate Image Edit Request - Qwen Image Edit Plus Lora API

**Request Code**
```http
POST https://gateway.pixazo.ai/qwen-image-edit-plus/v1/qwen-image-edit-plus-lora/generate
Content-Type: application/json

{
"prompt": "Close shot of a woman standing next to this car on this highway",
"image_urls": [
"https://example.com/reference1.png",
"https://example.com/reference2.png",
"https://example.com/reference3.png"
]
}
```

**Output**
```json
{
"request_id": "qwen-image-edit-plus-lora_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/qwen-image-edit-plus-lora_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
Webhook (Optional)

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Generate Image Edit Request
| Parameter | Required | Type | Description |
| --- | --- | --- | --- |
| prompt | Yes | string | Text prompt describing the desired image edit or generation |
| image_urls | Yes | array | Array of reference image URLs (1-10 images) |
| image_size | No | string or object | Output image size (see Image Size Options below) |
| num_inference_steps | No | number | Number of inference steps (higher = better quality, slower) |
| seed | No | number | Random seed for reproducibility |
| guidance_scale | No | number | CFG (Classifier Free Guidance) scale (1-20) |
| num_images | No | number | Number of images to generate (1-4) |
| output_format | No | string | Output format: "png" or "jpeg" |
| negative_prompt | No | string | What to avoid in the generated image |
| acceleration | No | string | Acceleration level: "none" or "regular" |
| enable_safety_checker | No | boolean | Enable NSFW content safety checker |
| webhook | No | string | Webhook URL for async notifications |
| webhook_events_filter | No | array | Event types to receive: ["start"], ["complete"], ["*"] |

**Example Request**
```json
{
"prompt": "Close shot of a woman standing next to this car on this highway",
"image_urls": [
"https://example.com/reference1.png",
"https://example.com/reference2.png",
"https://example.com/reference3.png"
],
"image_size": "square_hd",
"num_inference_steps": 50,
"guidance_scale": 4,
"num_images": 1,
"output_format": "png",
"negative_prompt": " ",
"acceleration": "regular",
"enable_safety_checker": true
}
```

**Response**
```json
{
"request_id": "qwen-image-edit-plus-lora_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/qwen-image-edit-plus-lora_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```

**Request Headers**
| Header | Value |
| --- | --- |
| Content-Type | application/json |
| --- | --- |

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
Error Responses

Queue system errors and model validation errors.

Queue System Errors
// 402 — Insufficient balance
```json
{
"error": "Insufficient Balance",
"message": "Your wallet does not have enough balance."
}
```
// 400 — Model not found
```json
{
"error": "Model not found",
"message": "Model 'qwen-image-edit-plus-lora' not found or is disabled"
}
```
Error via Status/Webhook
```json
{
"request_id": "qwen-image-edit-plus-lora_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "ERROR",
"model_id": "qwen-image-edit-plus-lora",
"error": "Description of the error",
"output": null
}
```
Retrieving Results

Poll the universal status endpoint to check progress and retrieve results.

Endpoint
```http
GET https://gateway.pixazo.ai/v2/requests/status/{request_id}
Ocp-Apim-Subscription-Key: YOUR_API_KEY
cURL Example
curl -H "Ocp-Apim-Subscription-Key: YOUR_API_KEY" \
"https://gateway.pixazo.ai/v2/requests/status/qwen-image-edit-plus-lora_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
Response (Completed)
{
"request_id": "qwen-image-edit-plus-lora_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "COMPLETED",
"model_id": "qwen-image-edit-plus-lora",
"error": null,
"output": {
"media_url": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/v1/qwen-image-edit-plus-lora_019dxxxx-xxxx/output.ext"
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
| --- | --- | --- |
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

Qwen LoRA v1 Generate (LoRA) API Pricing
| Resolution | Price (USD) |
| All Resolution | $0.055 |

#### Qwen LoRA v1 Training API Documentation
`https://gateway.pixazo.ai/qwen-image-edit-plus-trainer/v1`

**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| --- | --- | --- | --- |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |

#### Training Request - Qwen Image Edit Plus Trainer API

**Request Code**
```http
POST https://gateway.pixazo.ai/qwen-image-edit-plus-trainer/v1/qwen-image-edit-plus-trainer/generate
Content-Type: application/json
Ocp-Apim-Subscription-Key: your-subscription-key

{
"image_data_url": "https://example.com/lighting-training.zip",
"steps": 1000
}
```

**Output**
```json
{
"request_id": "qwen-image-edit-plus-trainer_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/qwen-image-edit-plus-trainer_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
Webhook (Optional)

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Training Request
| Parameter | Required | Type | Description |
| --- | --- | --- | --- |
| image_data_url | Yes | string | URL to ZIP archive containing training image pairs |
| learning_rate | No | number | Learning rate for LoRA parameters (0.0-1.0) |
| steps | No | number | Number of training steps (1-10000) |
| default_caption | No | string | Default caption when caption files are missing |
| reference_image_count | No | number | Number of reference images per entry (1-10) |
| webhook | No | string | Webhook URL for training completion notifications |
| webhook_events_filter | No | array | Event types to receive ("*" for all) |

**Example Request**
```json
{
"image_data_url": "https://example.com/lighting-training.zip",
"learning_rate": 0.0002,
"steps": 2000,
"default_caption": "professional cinematic lighting with dramatic shadows",
"reference_image_count": 2,
"webhook": "https://your-domain.com/training-complete"
}
```

**Response**
```json
{
"request_id": "qwen-image-edit-plus-trainer_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/qwen-image-edit-plus-trainer_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```

**Request Headers**
| Header | Value |
| --- | --- |
| Content-Type | application/json |
| --- | --- |
| Ocp-Apim-Subscription-Key | Your subscription key |

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
Error Responses

Queue system errors and model validation errors.

Queue System Errors
// 402 — Insufficient balance
```json
{
"error": "Insufficient Balance",
"message": "Your wallet does not have enough balance."
}
```
// 400 — Model not found
```json
{
"error": "Model not found",
"message": "Model 'qwen-image-edit-plus-trainer' not found or is disabled"
}
```
Error via Status/Webhook
```json
{
"request_id": "qwen-image-edit-plus-trainer_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "ERROR",
"model_id": "qwen-image-edit-plus-trainer",
"error": "Description of the error",
"output": null
}
```
Retrieving Results

Poll the universal status endpoint to check progress and retrieve results.

Endpoint
```http
GET https://gateway.pixazo.ai/v2/requests/status/{request_id}
Ocp-Apim-Subscription-Key: YOUR_API_KEY
cURL Example
curl -H "Ocp-Apim-Subscription-Key: YOUR_API_KEY" \
"https://gateway.pixazo.ai/v2/requests/status/qwen-image-edit-plus-trainer_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
Response (Completed)
{
"request_id": "qwen-image-edit-plus-trainer_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "COMPLETED",
"model_id": "qwen-image-edit-plus-trainer",
"error": null,
"output": {
"media_url": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/v1/qwen-image-edit-plus-trainer_019dxxxx-xxxx/output.ext"
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
| --- | --- | --- |
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

Qwen LoRA v1 Training API Pricing
| Resolution | Price (USD) |
| All Resolution | $0.04 |

#### Qwen LoRA v1 Layered Image API Documentation
`https://gateway.pixazo.ai/qwen-image-layered/v1`

**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| --- | --- | --- | --- |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |

#### Qwen Image Layered generate request - Qwen Image Layered API

**Request Code**
```http
POST https://gateway.pixazo.ai/qwen-image-layered/v1/qwen-image-layered-request
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY

{
"image_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/car_race.jpeg",
"num_inference_steps": 28,
"guidance_scale": 5.0,
"num_images": 1,
"enable_safety_checker": true,
"output_format": "png",
"acceleration": "regular"
}
```

**Output**
```json
{
"request_id": "qwen-image-layered_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/qwen-image-layered_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
Webhook (Optional)

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Qwen Image Layered generate request
| Parameter | Required | Type | Description |
| --- | --- | --- | --- |
| image_url | Yes | string | Publicly accessible URL of the input image to be decomposed into layers. Must be reachable by the server. |
| num_inference_steps | No | integer | Number of denoising steps during the decomposition process. Higher values yield finer details at the cost of processing time. |
| guidance_scale | No | number | Controls the strength of the decomposition guidance. Higher values enforce stronger adherence to input structure. |
| num_images | No | integer | Number of layer sets to generate (currently supports only 1). |
| enable_safety_checker | No | boolean | Enables content safety filtering to block potentially harmful or inappropriate outputs. |
| output_format | No | string | Output format for the generated layers. Only "png" is currently supported to preserve transparency. |
| acceleration | No | string | Processing mode. Use "regular" for standard quality, "fast" for quicker but lower resolution outputs. |

**Example Request**
```json
{
"image_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/car_race.jpeg",
"num_inference_steps": 28,
"guidance_scale": 5.0,
"num_images": 1,
"enable_safety_checker": true,
"output_format": "png",
"acceleration": "regular"
}
```

**Response**
```json
{
"request_id": "qwen-image-layered_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/qwen-image-layered_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```

**Request Headers**
| Header | Value |
| --- | --- |
| Content-Type | application/json |
| --- | --- |
| Cache-Control | no-cache |
| Ocp-Apim-Subscription-Key | YOUR_SUBSCRIPTION_KEY |

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
Error Responses

Queue system errors and model validation errors.

Queue System Errors
// 402 — Insufficient balance
```json
{
"error": "Insufficient Balance",
"message": "Your wallet does not have enough balance."
}
```
// 400 — Model not found
```json
{
"error": "Model not found",
"message": "Model 'qwen-image-layered' not found or is disabled"
}
```
Error via Status/Webhook
```json
{
"request_id": "qwen-image-layered_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "ERROR",
"model_id": "qwen-image-layered",
"error": "Description of the error",
"output": null
}
```
Retrieving Results

Poll the universal status endpoint to check progress and retrieve results.

Endpoint
```http
GET https://gateway.pixazo.ai/v2/requests/status/{request_id}
Ocp-Apim-Subscription-Key: YOUR_API_KEY
cURL Example
curl -H "Ocp-Apim-Subscription-Key: YOUR_API_KEY" \
"https://gateway.pixazo.ai/v2/requests/status/qwen-image-layered_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
Response (Completed)
{
"request_id": "qwen-image-layered_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "COMPLETED",
"model_id": "qwen-image-layered",
"error": null,
"output": {
"media_url": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/v1/qwen-image-layered_019dxxxx-xxxx/output.ext"
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
| --- | --- | --- |
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

Qwen LoRA v1 Layered Image API Pricing
| Resolution | Price (USD) |
| All Resolution | $0.05 |
API 
---
### SDXL 1.0 API, SDXL Turbo, 1.0 Lightning (Free) API - AI Image Generation APIs
**Page:** https://www.pixazo.ai/models/sdxl


by Stability AI

SDXL 1.0 API, developers can access SDXL variants including Lightning and Turbo for different speed and quality tradeoffs. The API supports the widely-adopted SDXL ecosystem, ensuring compatibility with existing workflows and fine-tuned models.

SDXL Turbo
SDXL Base 1.0 - FREE
SDXL Lightning
Generate Image


Generate Image

**Request Code**

**Request Parameters**

**Example Request**

**Response**

**Request Headers**

**Response Handling**

#### SDXL Turbo Generate Image API Documentation

**Request Code**
```http
POST https://gateway.pixazo.ai/sdxlTurbo/v2/getData
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY

{
"prompt": "Create a detailed illustration of a sparrow perched on a branch. The bird should be depicted with its distinctive brown and gray feathers, a small beak, and bright, curious eyes. Surround the sparrow with a vibrant background of spring leaves and soft sunlight filtering through the trees, capturing the essence of a tranquil morning in nature.",
"height": 768,
"width": 768,
"num_inference_steps": 50,
"guidance_scale": 7.5,
"seed": 42
}
```

**Output**
```json
{
"output": "https://d3re0c8wemxg38.cloudfront.net/output_mme/8891449d-7bbd-4150-9a56-86ff51378640.jpeg",
"status": "complete",
"message": "In progress"
}
```
Request Parameters - SDXL Turbo Get Image
| Parameter | Required | Type | Description |
| --- | --- | --- | --- |
| prompt | Yes | string | The text query that instructs the AI model on what kind of content to generate. In this case, "Create a detailed illustration of a sparrow perched on a branch..." |
| height | No | integer | The height in pixels of the generated image. Default: 768 |
| width | No | integer | The width in pixels of the generated image. Default: 768 |
| num_inference_steps | Yes | integer | The number of diffusion steps; higher values can improve quality but take longer. Minimum: 1, Maximum: 50 |
| guidance_scale | Yes | float | Determines the adherence to the prompt; higher values encourage better alignment with the prompt. Minimum: 0, Maximum: 15 |
| seed | No | integer | A seed for random number generation to ensure reproducibility of the results. Default is usually set by the system if not specified |

**Example Request**
```json
{
"prompt": "Create a detailed illustration of a sparrow perched on a branch. The bird should be depicted with its distinctive brown and gray feathers, a small beak, and bright, curious eyes. Surround the sparrow with a vibrant background of spring leaves and soft sunlight filtering through the trees, capturing the essence of a tranquil morning in nature.",
"height": 768,
"width": 768,
"num_inference_steps": 50,
"guidance_scale": 7.5,
"seed": 42
}
```

**Response**
```json
{
"output": "https://d3re0c8wemxg38.cloudfront.net/output_mme/8891449d-7bbd-4150-9a56-86ff51378640.jpeg",
"status": "complete",
"message": "In progress"
}
```

**Request Headers**
| Header | Value |
| --- | --- |
| Content-Type | application/json |
| --- | --- |
| Cache-Control | no-cache |
| Ocp-Apim-Subscription-Key | YOUR_SUBSCRIPTION_KEY |

**Response Handling**

Common status codes for SDXL Turbo Get Image.

| Code | Meaning |
| 200 | Success |
| 400 | Bad Request |
| 401 | Unauthorized |
| 403 | Forbidden |
| 404 | Not Found |
| 429 | Too Many Requests |
| 500 | Internal Server Error |
SDXL Turbo Generate Image API Pricing
| Resolution | Price (USD) |
| All Resolution | $0 |
2. SDXL Base 1.0

#### SDXL Base 1.0 Generate Image API Documentation

**Request Code**
```http
POST https://gateway.pixazo.ai/getImage/v1/getSDXLImage
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY

{
"prompt": "High-resolution, realistic image of a sparrow bird perched on a blooming cherry blossom branch during springtime. The sparrow feathers should be finely detailed with natural colors, including shades of brown and white. The background should be soft-focused with a clear blue sky, creating a serene and peaceful atmosphere.",
"negative_prompt": "Low-quality, blurry image, with any other birds or animals. Avoid abstract or cartoonish styles, dark or gloomy atmosphere, unnecessary objects or distractions in the background, harsh lighting, and unnatural colors.",
"height": 1024,
"width": 1024,
"num_steps": 20,
"guidance_scale": 5,
"seed": 40
}
```

**Output**
```json
{
"imageUrl": ""
}
```
Request Parameters - Get Image
| Parameter | Required | Type | Description |
| --- | --- | --- | --- |
| prompt | Yes | String | The main instruction for the image generation |
| negative_prompt | No | String | A prompt to specify what should be avoided in the image |
| height | No | Integer | The height of the output image (e.g., 1024) |
| width | No | Integer | The width of the output image (e.g., 1024) |
| num_steps | No | Integer | The number of steps for the generation process (e.g., 20) |
| guidance_scale | No | Integer | The guidance scale for the generation (e.g., 5) |
| seed | No | Integer | The seed value for random number generation to ensure reproducibility |

**Example Request**
```json
{
"prompt": "High-resolution, realistic image of a sparrow bird perched on a blooming cherry blossom branch during springtime. The sparrow feathers should be finely detailed with natural colors, including shades of brown and white. The background should be soft-focused with a clear blue sky, creating a serene and peaceful atmosphere.",
"negative_prompt": "Low-quality, blurry image, with any other birds or animals. Avoid abstract or cartoonish styles, dark or gloomy atmosphere, unnecessary objects or distractions in the background, harsh lighting, and unnatural colors.",
"height": 1024,
"width": 1024,
"num_steps": 20,
"guidance_scale": 5,
"seed": 40
}
```

**Response**
```json
{
"imageUrl": ""
}
```

**Request Headers**
| Header | Value |
| --- | --- |
| Content-Type | application/json |
| --- | --- |
| Cache-Control | no-cache |
| Ocp-Apim-Subscription-Key | YOUR_SUBSCRIPTION_KEY |

**Response Handling**

Common status codes for Get Image.

| Code | Meaning |
| 200 | Success |
| 400 | Bad Request |
| 401 | Unauthorized |
| 403 | Forbidden |
| 404 | Not Found |
| 429 | Too Many Requests |
| 500 | Internal Server Error |
SDXL Base 1.0 Generate Image API Pricing
| Resolution | Price (USD) |
| All Resolution | $0 |
3. SDXL Lightning

#### SDXL Lightning Generate Image API Documentation

**Request Code**
```http
POST https://gateway.pixazo.ai/sdxl_lightning/getImage/v1/getSDXLImage
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY

{
"prompt": "High-resolution, realistic image of a sparrow bird perched on a blooming cherry blossom branch during springtime. The sparrow feathers should be finely detailed with natural colors, including shades of brown and white. The background should be soft-focused with a clear blue sky, creating a serene and peaceful atmosphere.",
"negativePrompt": "Low-quality, blurry image, with any other birds or animals. Avoid abstract or cartoonish styles, dark or gloomy atmosphere, unnecessary objects or distractions in the background, harsh lighting, and unnatural colors.",
"height": 1024,
"width": 1024,
"num_steps": 20,
"guidance": 5,
"seed": 42
}
```

**Output**
```json
{
"imageUrl": ""
}
```
Request Parameters - Get Image
| Parameter | Required | Type | Description |
| --- | --- | --- | --- |
| prompt | Yes | String | The main instruction for the image generation |
| negativePrompt | No | String | A prompt to specify what should be avoided in the image |
| height | No | Integer | The height of the output image (e.g., 1024) |
| width | No | Integer | The width of the output image (e.g., 1024) |
| num_steps | No | Integer | The number of steps for the generation process (e.g., 20) |
| guidance | No | Integer | The guidance scale for the generation (e.g., 5) |
| seed | No | Integer | The seed value for random number generation to ensure reproducibility |

**Example Request**
```json
{
"prompt": "High-resolution, realistic image of a sparrow bird perched on a blooming cherry blossom branch during springtime. The sparrow feathers should be finely detailed with natural colors, including shades of brown and white. The background should be soft-focused with a clear blue sky, creating a serene and peaceful atmosphere.",
"negativePrompt": "Low-quality, blurry image, with any other birds or animals. Avoid abstract or cartoonish styles, dark or gloomy atmosphere, unnecessary objects or distractions in the background, harsh lighting, and unnatural colors.",
"height": 1024,
"width": 1024,
"num_steps": 20,
"guidance": 5,
"seed": 42
}
```

**Response**
```json
{
"imageUrl": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/sdxl_lightning/prompt-355182775-1724841428119-422172.png"
}
```

**Request Headers**
| Header | Value |
| --- | --- |
| Content-Type | application/json |
| --- | --- |
| Cache-Control | no-cache |
| Ocp-Apim-Subscription-Key | YOUR_SUBSCRIPTION_KEY |

**Response Handling**

Common status codes for Get Image.

| Code | Meaning |
| 200 | Success |
| 400 | Bad Request |
| 401 | Unauthorized |
| 403 | Forbidden |
| 404 | Not Found |
| 429 | Too Many Requests |
| 500 | Internal Server Error |
SDXL Lightning Generate Image API Pricing
| Resolution | Price (USD) |
| All Resolution | $0 |
API 
---
### Stable Diffusion 3.5 API, Stable Diffusion 3.0, 1.5, 1.0 Turbo, 1.0 Video (Free) API - AI Image & Video Generation APIs
**Page:** https://www.pixazo.ai/models/stable-diffusion


by Stability AI

Stable Diffusion 3.5 API, developers can access multiple Stable Diffusion versions including 3.0, 3.5, and specialized models for inpainting and video generation. The API provides the flexibility and control that made Stable Diffusion the standard for AI image generation in production applications.

Stable Diffusion v3.5
Stable Diffusion v3
Stable Diffusion XL v1.0 - FREE
Stable Diffusion XL Lightning
Stable Diffusion Inpainting - FREE
Text To Image


Text To Image

**Request Code**

**Request Parameters**

**Example Request**

**Response**

**Request Headers**

**Response Handling**

#### Stable Diffusion v3.5 Text To Image API Documentation

**Request Code**
```http
POST https://gateway.pixazo.ai/sd3-5/v1/r-sd-3-5-large
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: your-subscription-key

{
"prompt": "~*~aesthetic~*~ #boho #fashion, full-body 30-something woman laying on microfloral grass, candid pose, overlay reads Stable Diffusion 3.5, cheerful cursive typography font",
"aspect_ratio": "1:1",
"cfg": 4.5,
"steps": 40,
"output_format": "webp",
"output_quality": 90,
"prompt_strength": 0.85
}
```

**Output**
```json
{
"id": "j8qc0mvk8drmy0cxe4wvcy8ajw",
"status": "succeeded",
"output": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/sd-3-5-large/prompt-1775721091831-439186.webp"
}
```
Request Parameters - Get Image
| Parameter | Required | Type | Description |
| --- | --- | --- | --- |
| prompt | Yes | string | The text prompt describing the desired image |
| aspect_ratio | Yes | string | Aspect ratio of the output image (e.g., "1:1", "16:9") |
| cfg | Yes | number | Classifier-Free Guidance scale, controls prompt adherence (1.0-20.0) |
| steps | Yes | number | Number of inference steps (20-100) |
| output_format | Yes | string | Output format: "webp", "jpg", "png", "jpeg" |
| output_quality | Yes | number | Quality level for compressed formats (1-100) |
| prompt_strength | Yes | number | Strength of prompt influence (0.0-1.0) |

**Example Request**
```json
{
"prompt": "~*~aesthetic~*~ #boho #fashion, full-body 30-something woman laying on microfloral grass, candid pose, overlay reads Stable Diffusion 3.5, cheerful cursive typography font",
"aspect_ratio": "1:1",
"cfg": 4.5,
"steps": 40,
"output_format": "webp",
"output_quality": 90,
"prompt_strength": 0.85
}
```

**Response**
```json
{
"id": "j8qc0mvk8drmy0cxe4wvcy8ajw",
"status": "succeeded",
"output": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/sd-3-5-large/prompt-1775721091831-439186.webp"
}
```

**Request Headers**
| Header | Value |
| --- | --- |
| Content-Type | application/json |
| --- | --- |
| Cache-Control | no-cache |
| Ocp-Apim-Subscription-Key | your-subscription-key |

**Response Handling**

Common status codes for Get Image.

| Code | Meaning |
| 200 | Success |
| 400 | Bad Request |
| 401 | Unauthorized |
| 403 | Forbidden |
| 404 | Not Found |
| 429 | Too Many Requests |
| 500 | Internal Server Error |
Stable Diffusion v3.5 Text To Image API Pricing
| Resolution | Price (USD) |
| All Resolution | $0.2 |
2. Stable Diffusion v3

#### Stable Diffusion v3 Text To Image API Documentation

**Request Code**
```http
POST https://gateway.pixazo.ai/sd3/v1/getData
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY

{
"prompt": "Picture a sleek, futuristic car racing through a neon-lit cityscape, its engine humming efficiently as it blurs past digital billboards. The driver skillfully navigates the glowing streets, aiming for victory in this high-tech, adrenaline-fueled race of tomorrow.",
"negativePrompt": "dark, blurry",
"steps": 28,
"cfg": 4.0,
"aspect_ratio": "3:2",
"output_format": "jpg",
"output_quality": 90,
"prompt_strength": 0.85
}
```

**Output**
```json
{
"output": "https://example.com/image.jpg"
}
```
Request Parameters - Get Data
| Parameter | Required | Type | Description |
| --- | --- | --- | --- |
| prompt | Yes | string | The text query that instructs the AI model on what kind of content to generate. In this case, "womens street skateboarding final in Paris Olympics 2024." |
| negativePrompt | No | string | Negative prompts do not really work in SD3. Using a negative prompt will change your output in unpredictable ways. |
| steps | No | integer | The number of steps for the transformation process. Default: 28, (minimum: 1, maximum: 28) |
| cfg | No | decimal | The guidance scale tells the model how similar the output should be to the prompt. Default: 3.5, (minimum: 0, maximum: 20) |
| aspect_ratio | No | string | The aspect ratio of your output image. This value is ignored if you are using an input image. Default: "1:1" |
| output_format | No | string | Format of the output images. Default: "webp" |
| output_quality | No | integer | Quality of the output images, from 0 to 100. 100 is best quality, 0 is lowest quality. Default: 90, (minimum: 0, maximum: 100) |
| prompt_strength | No | decimal | Prompt strength (or denoising strength) when using image to image. 1.0 corresponds to full destruction of information in image. Default: 0.85, (minimum: 0, maximum: 1) |

**Example Request**
```json
{
"prompt": "Picture a sleek, futuristic car racing through a neon-lit cityscape, its engine humming efficiently as it blurs past digital billboards. The driver skillfully navigates the glowing streets, aiming for victory in this high-tech, adrenaline-fueled race of tomorrow.",
"negativePrompt": "dark, blurry",
"steps": 28,
"cfg": 4.0,
"aspect_ratio": "3:2",
"output_format": "jpg",
"output_quality": 90,
"prompt_strength": 0.85
}
```

**Response**
```json
{
"output": "https://example.com/image.jpg"
}
```

**Request Headers**
| Header | Value |
| --- | --- |
| Content-Type | application/json |
| --- | --- |
| Cache-Control | no-cache |
| Ocp-Apim-Subscription-Key | YOUR_SUBSCRIPTION_KEY |

**Response Handling**

Common status codes for Get Data.

| Code | Meaning |
| 200 | Success |
| 400 | Bad Request |
| 401 | Unauthorized |
| 403 | Forbidden |
| 404 | Not Found |
| 429 | Too Many Requests |
| 500 | Internal Server Error |
Stable Diffusion v3 Text To Image API Pricing
| Resolution | Price (USD) |
| All Resolution | $0.2 |
3. Stable Diffusion XL v1.0

#### Stable Diffusion XL v1.0 Text To Image API Documentation

**Request Code**
```http
POST https://gateway.pixazo.ai/getImage/v1/getSDXLImage
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY

{
"prompt": "High-resolution, realistic image of a sparrow bird perched on a blooming cherry blossom branch during springtime. The sparrow feathers should be finely detailed with natural colors, including shades of brown and white. The background should be soft-focused with a clear blue sky, creating a serene and peaceful atmosphere.",
"negative_prompt": "Low-quality, blurry image, with any other birds or animals. Avoid abstract or cartoonish styles, dark or gloomy atmosphere, unnecessary objects or distractions in the background, harsh lighting, and unnatural colors.",
"height": 1024,
"width": 1024,
"num_steps": 20,
"guidance_scale": 5,
"seed": 40
}
```

**Output**
```json
{
"imageUrl": ""
}
```
Request Parameters - Get Image
| Parameter | Required | Type | Description |
| --- | --- | --- | --- |
| prompt | Yes | String | The main instruction for the image generation |
| negative_prompt | No | String | A prompt to specify what should be avoided in the image |
| height | No | Integer | The height of the output image (e.g., 1024) |
| width | No | Integer | The width of the output image (e.g., 1024) |
| num_steps | No | Integer | The number of steps for the generation process (e.g., 20) |
| guidance_scale | No | Integer | The guidance scale for the generation (e.g., 5) |
| seed | No | Integer | The seed value for random number generation to ensure reproducibility |

**Example Request**
```json
{
"prompt": "High-resolution, realistic image of a sparrow bird perched on a blooming cherry blossom branch during springtime. The sparrow feathers should be finely detailed with natural colors, including shades of brown and white. The background should be soft-focused with a clear blue sky, creating a serene and peaceful atmosphere.",
"negative_prompt": "Low-quality, blurry image, with any other birds or animals. Avoid abstract or cartoonish styles, dark or gloomy atmosphere, unnecessary objects or distractions in the background, harsh lighting, and unnatural colors.",
"height": 1024,
"width": 1024,
"num_steps": 20,
"guidance_scale": 5,
"seed": 40
}
```

**Response**
```json
{
"imageUrl": ""
}
```

**Request Headers**
| Header | Value |
| --- | --- |
| Content-Type | application/json |
| --- | --- |
| Cache-Control | no-cache |
| Ocp-Apim-Subscription-Key | YOUR_SUBSCRIPTION_KEY |

**Response Handling**

Common status codes for Get Image.

| Code | Meaning |
| 200 | Success |
| 400 | Bad Request |
| 401 | Unauthorized |
| 403 | Forbidden |
| 404 | Not Found |
| 429 | Too Many Requests |
| 500 | Internal Server Error |
Stable Diffusion XL v1.0 Text To Image API Pricing
| Resolution | Price (USD) |
| All Resolution | $0 |
4. Stable Diffusion XL Lightning

#### Stable Diffusion XL Lightning Text To Image API Documentation

**Request Code**
```http
POST https://gateway.pixazo.ai/sdxl_lightning/getImage/v1/getSDXLImage
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY

{
"prompt": "High-resolution, realistic image of a sparrow bird perched on a blooming cherry blossom branch during springtime. The sparrow feathers should be finely detailed with natural colors, including shades of brown and white. The background should be soft-focused with a clear blue sky, creating a serene and peaceful atmosphere.",
"negativePrompt": "Low-quality, blurry image, with any other birds or animals. Avoid abstract or cartoonish styles, dark or gloomy atmosphere, unnecessary objects or distractions in the background, harsh lighting, and unnatural colors.",
"height": 1024,
"width": 1024,
"num_steps": 20,
"guidance": 5,
"seed": 42
}
```

**Output**
```json
{
"imageUrl": ""
}
```
Request Parameters - Get Image
| Parameter | Required | Type | Description |
| --- | --- | --- | --- |
| prompt | Yes | String | The main instruction for the image generation |
| negativePrompt | No | String | A prompt to specify what should be avoided in the image |
| height | No | Integer | The height of the output image (e.g., 1024) |
| width | No | Integer | The width of the output image (e.g., 1024) |
| num_steps | No | Integer | The number of steps for the generation process (e.g., 20) |
| guidance | No | Integer | The guidance scale for the generation (e.g., 5) |
| seed | No | Integer | The seed value for random number generation to ensure reproducibility |

**Example Request**
```json
{
"prompt": "High-resolution, realistic image of a sparrow bird perched on a blooming cherry blossom branch during springtime. The sparrow feathers should be finely detailed with natural colors, including shades of brown and white. The background should be soft-focused with a clear blue sky, creating a serene and peaceful atmosphere.",
"negativePrompt": "Low-quality, blurry image, with any other birds or animals. Avoid abstract or cartoonish styles, dark or gloomy atmosphere, unnecessary objects or distractions in the background, harsh lighting, and unnatural colors.",
"height": 1024,
"width": 1024,
"num_steps": 20,
"guidance": 5,
"seed": 42
}
```

**Response**
```json
{
"imageUrl": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/sdxl_lightning/prompt-355182775-1724841428119-422172.png"
}
```

**Request Headers**
| Header | Value |
| --- | --- |
| Content-Type | application/json |
| --- | --- |
| Cache-Control | no-cache |
| Ocp-Apim-Subscription-Key | YOUR_SUBSCRIPTION_KEY |

**Response Handling**

Common status codes for Get Image.

| Code | Meaning |
| 200 | Success |
| 400 | Bad Request |
| 401 | Unauthorized |
| 403 | Forbidden |
| 404 | Not Found |
| 429 | Too Many Requests |
| 500 | Internal Server Error |
Stable Diffusion XL Lightning Text To Image API Pricing
| Resolution | Price (USD) |
| All Resolution | $0 |

#### Stable Diffusion XL Lightning Text To Image(Stream) API Documentation

**Request Code**
```http
POST https://gateway.pixazo.ai/sdxl_lightning/getImage/v1/getSDXLImageStream
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY
{
"prompt": "High-resolution, realistic image of a sparrow bird perched on a blooming cherry blossom branch during springtime. The sparrow feathers should be finely detailed with natural colors, including shades of brown and white. The background should be soft-focused with a clear blue sky, creating a serene and peaceful atmosphere.",
"negativePrompt": "Low-quality, blurry image, with any other birds or animals. Avoid abstract or cartoonish styles, dark or gloomy atmosphere, unnecessary objects or distractions in the background, harsh lighting, and unnatural colors.",
"height": 1024,
"width": 1024,
"num_steps": 20,
"guidance": 5,
"seed": 42
}
```

**Output**
```json
{
"imageUrl": "https://example.com/image.jpg"
}
```
Request Parameters - Get Image Stream
| Parameter | Required | Type | Description |
| --- | --- | --- | --- |
| prompt | Yes | String | The main instruction for the image generation |
| negativePrompt | No | String | A prompt to specify what should be avoided in the image |
| height | No | Integer | The height of the output image (e.g., 1024) |
| width | No | Integer | The width of the output image (e.g., 1024) |
| num_steps | No | Integer | The number of steps for the generation process (e.g., 20) |
| guidance | No | Integer | The guidance scale for the generation (e.g., 5) |
| seed | No | Integer | The seed value for random number generation to ensure reproducibility |

**Example Request**
```json
{
"prompt": "High-resolution, realistic image of a sparrow bird perched on a blooming cherry blossom branch during springtime. The sparrow feathers should be finely detailed with natural colors, including shades of brown and white. The background should be soft-focused with a clear blue sky, creating a serene and peaceful atmosphere.",
"negativePrompt": "Low-quality, blurry image, with any other birds or animals. Avoid abstract or cartoonish styles, dark or gloomy atmosphere, unnecessary objects or distractions in the background, harsh lighting, and unnatural colors.",
"height": 1024,
"width": 1024,
"num_steps": 20,
"guidance": 5,
"seed": 42
}
```

**Response**
```json
{
"imageUrl": "https://example.com/image.jpg"
}
```

**Request Headers**
| Header | Value |
| --- | --- |
| Content-Type | application/json |
| --- | --- |
| Cache-Control | no-cache |
| Ocp-Apim-Subscription-Key | YOUR_SUBSCRIPTION_KEY |

**Response Handling**

Common status codes for Get Image Stream.

| Code | Meaning |
| 200 | Success |
| 400 | Bad Request |
| 401 | Unauthorized |
| 403 | Forbidden |
| 404 | Not Found |
| 429 | Too Many Requests |
| 500 | Internal Server Error |
Stable Diffusion XL Lightning Text To Image(Stream) API Pricing
| Resolution | Price (USD) |
| All Resolution | $0 |
5. Stable Diffusion Inpainting

#### Stable Diffusion Inpainting Inpaint Image API Documentation

**Request Code**
```http
POST https://gateway.pixazo.ai/inpainting/v1/getImage
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY

{
"prompt": "Change to a lion",
"imageUrl": "https://pub-1fb693cb11cc46b2b2f656f51e015a2c.r2.dev/dog.png",
"maskUrl": "https://pub-1fb693cb11cc46b2b2f656f51e015a2c.r2.dev/dog-mask.png",
"negative_prompt": "watermark",
"height": 1024,
"width": 1024,
"num_steps": 20,
"guidance": 5,
"seed": 42
}
```

**Output**
```json
{
"imageUrl": ""
}
```
Request Parameters - Get Image
| Parameter | Required | Type | Description |
| --- | --- | --- | --- |
| prompt | Yes | String | The main instruction for the image transformation |
| imageUrl | No | URL | The URL of the initial image to be transformed |
| maskUrl | No | URL | The URL of the mask image |
| negativePrompt | No | String | A prompt to specify what should be avoided in the image |
| height | No | Integer | The height of the output image (e.g., 1024) |
| width | No | Integer | The width of the output image (e.g., 1024) |
| num_steps | No | Integer | The number of steps for the transformation process (e.g., 20) |
| guidance | No | Integer | The guidance scale for the transformation (e.g., 5) |
| seed | No | Integer | The seed value for random number generation to ensure reproducibility |

**Example Request**
```json
{
"prompt": "Change to a lion",
"imageUrl": "https://pub-1fb693cb11cc46b2b2f656f51e015a2c.r2.dev/dog.png",
"maskUrl": "https://pub-1fb693cb11cc46b2b2f656f51e015a2c.r2.dev/dog-mask.png",
"negative_prompt": "watermark",
"height": 1024,
"width": 1024,
"num_steps": 20,
"guidance": 5,
"seed": 42
}
```

**Response**
```json
{
"imageUrl": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/sdxl_lightning/prompt-355182775-1724841428119-422172.png"
}
```

**Request Headers**
| Header | Value |
| --- | --- |
| Content-Type | application/json |
| --- | --- |
| Cache-Control | no-cache |
| Ocp-Apim-Subscription-Key | YOUR_SUBSCRIPTION_KEY |

**Response Handling**

Common status codes for Get Image.

| Code | Meaning |
| 200 | Success |
| 400 | Bad Request |
| 401 | Unauthorized |
| 403 | Forbidden |
| 404 | Not Found |
| 429 | Too Many Requests |
| 500 | Internal Server Error |
Stable Diffusion Inpainting Inpaint Image API Pricing
| Resolution | Price (USD) |
| All Resolution | $0 |

#### Stable Diffusion Inpainting Inpaint Image(Stream) API Documentation

**Request Code**
```http
POST https://gateway.pixazo.ai/inpainting/v1/getImageStream
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY

{
"prompt": "Change to a lion"
}
```

**Output**
```json
{
"imageUrl": ""
}
```
Request Parameters - Get Image Stream
| Parameter | Required | Type | Description |
| --- | --- | --- | --- |
| prompt | Yes | String | The main instruction for the image transformation |
| imageUrl | No | URL | The URL of the initial image to be transformed |
| maskUrl | No | URL | The URL of the mask image |
| negativePrompt | No | String | A prompt to specify what should be avoided in the image |
| height | No | Integer | The height of the output image (e.g., 1024) |
| width | No | Integer | The width of the output image (e.g., 1024) |
| num_steps | No | Integer | The number of steps for the transformation process (e.g., 20) |
| guidance | No | Integer | The guidance scale for the transformation (e.g., 5) |
| seed | No | Integer | The seed value for random number generation to ensure reproducibility |

**Example Request**
```json
{
"prompt": "Change to a lion",
"imageUrl": "https://pub-1fb693cb11cc46b2b2f656f51e015a2c.r2.dev/dog.png",
"maskUrl": "https://pub-1fb693cb11cc46b2b2f656f51e015a2c.r2.dev/dog-mask.png",
"negativePrompt": "watermark",
"height": 1024,
"width": 1024,
"num_steps": 20,
"guidance": 5,
"seed": 42
}
```

**Response**
```json
{
"imageUrl": "https://example.com/generated-image-12345.png"
}
```

**Request Headers**
| Header | Value |
| --- | --- |
| Content-Type | application/json |
| --- | --- |
| Cache-Control | no-cache |
| Ocp-Apim-Subscription-Key | YOUR_SUBSCRIPTION_KEY |

**Response Handling**

Common status codes for Get Image Stream.

| Code | Meaning |
| 200 | Success |
| 400 | Bad Request |
| 401 | Unauthorized |
| 403 | Forbidden |
| 404 | Not Found |
| 429 | Too Many Requests |
| 500 | Internal Server Error |
Stable Diffusion Inpainting Inpaint Image(Stream) API Pricing
| Resolution | Price (USD) |
| All Resolution | $0 |
API 
---
### Z-Image Turbo API - AI Image Generation APIs
**Page:** https://www.pixazo.ai/models/z-image


by Z-Image

Z-Image Turbo API, developers can generate images with reduced latency for interactive applications and high-volume workflows. The API balances speed and quality, suitable for real-time creative tools and production environments where generation time is critical.

Z-Image Turbo
Z-Image Base
Text To Image


Text To Image

**Request Code**

**Request Parameters**

**Example Request**

**Response**

**Request Headers**

**Response Handling**

#### Z-Image Turbo Text To Image API Documentation
`https://gateway.pixazo.ai/z-image-turbo-834/v1`

**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| --- | --- | --- | --- |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |

#### Generate Request - Z-Image Turbo API

**Request Code**
```http
POST https://gateway.pixazo.ai/z-image-turbo-834/v1/z-image-turbo-request
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY

{
"prompt": "A hyper-realistic close-up of an Omo Valley tribal elder, adorned with white chalk patterns and a headdress of dried flowers, seed pods, and bottle caps. Razor-sharp skin texture with every pore and wrinkle visible. A warm firelight glows in the elder's soulful eyes against a blurred, smoky hut interior, captured in a Leica M6 / Kodak Portra 400 film aesthetic.",
"image_size": "landscape_4_3",
"num_inference_steps": 8,
"num_images": 1,
"enable_safety_checker": true,
"output_format": "png",
"acceleration": "none"
}
```

**Output**
```json
{
"request_id": "z-image-turbo-834_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/z-image-turbo-834_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
Webhook (Optional)

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Generate Request
| Parameter | Required | Type | Description |
| --- | --- | --- | --- |
| prompt | Yes | string | A detailed textual description of the desired image. Be specific about subjects, styles, lighting, and composition for optimal results. |
| image_size | Yes | string | The aspect ratio and resolution of the output image. Supported values: `portrait_3_4`, `landscape_4_3`, `square_1_1`, `portrait_9_16`, `landscape_16_9`. |
| num_inference_steps | Optional | integer | Number of denoising steps during image generation. Lower values speed up generation but may reduce quality. Valid range: 1–50. |
| num_images | Optional | integer | Number of images to generate per request. Maximum allowed value is 4. |
| enable_safety_checker | Optional | boolean | Enables or disables content safety filtering. When enabled, potentially harmful or explicit content is blocked. |
| output_format | Optional | string | File format of the generated image. Supported formats: `png`, `jpeg`, `webp`. |
| acceleration | Optional | string | Specifies hardware acceleration mode. Currently only `none` is supported. |

**Example Request**
```json
{
"prompt": "A hyper-realistic close-up of an Omo Valley tribal elder, adorned with white chalk patterns and a headdress of dried flowers, seed pods, and bottle caps. Razor-sharp skin texture with every pore and wrinkle visible. A warm firelight glows in the elder's soulful eyes against a blurred, smoky hut interior, captured in a Leica M6 / Kodak Portra 400 film aesthetic.",
"image_size": "landscape_4_3",
"num_inference_steps": 8,
"num_images": 1,
"enable_safety_checker": true,
"output_format": "png",
"acceleration": "none"
}
```

**Response**
```json
{
"request_id": "z-image-turbo-834_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/z-image-turbo-834_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```

**Request Headers**
| Header | Value |
| --- | --- |
| Content-Type | application/json |
| --- | --- |
| Cache-Control | no-cache |
| Ocp-Apim-Subscription-Key | YOUR_SUBSCRIPTION_KEY |

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
Error Responses

Queue system errors and model validation errors.

Queue System Errors
// 402 — Insufficient balance
```json
{
"error": "Insufficient Balance",
"message": "Your wallet does not have enough balance."
}
```
// 400 — Model not found
```json
{
"error": "Model not found",
"message": "Model 'z-image-turbo-834' not found or is disabled"
}
```
Error via Status/Webhook
```json
{
"request_id": "z-image-turbo-834_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "ERROR",
"model_id": "z-image-turbo-834",
"error": "Description of the error",
"output": null
}
```
Retrieving Results

Poll the universal status endpoint to check progress and retrieve results.

Endpoint
```http
GET https://gateway.pixazo.ai/v2/requests/status/{request_id}
Ocp-Apim-Subscription-Key: YOUR_API_KEY
cURL Example
curl -H "Ocp-Apim-Subscription-Key: YOUR_API_KEY" \
"https://gateway.pixazo.ai/v2/requests/status/z-image-turbo-834_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
Response (Completed)
{
"request_id": "z-image-turbo-834_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "COMPLETED",
"model_id": "z-image-turbo-834",
"error": null,
"output": {
"media_url": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/v1/z-image-turbo-834_019dxxxx-xxxx/output.ext"
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
| --- | --- | --- |
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

Z-Image Turbo Text To Image API Pricing
| Resolution | Price (USD) |
| All Resolution | $0.008 |
2. Z-Image Base

#### Z-Image Base Text To Image API Documentation
`https://gateway.pixazo.ai/z-image-base/v1`

**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| --- | --- | --- | --- |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Z-Image base generate request - Z-Image base

**Request Code**
```http
POST https://gateway.pixazo.ai/z-image-base/v1/z-image-base-request
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY

{
"prompt": "Grandmother knitting by a window, an empty chair by her",
"image_size": "landscape_4_3"
}
```

**Output**
```json
{
"request_id": "z-image-base_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/z-image-base_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
Webhook (Optional)

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Z-Image base generate request
| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| prompt | string | Yes | — | Text description describing the desired image. Be specific for best results. |
| image_size | string | Yes | — | Aspect ratio of output image. Supported values: landscape_4_3, portrait_3_4, square_1_1, landscape_16_9, portrait_9_16. |
| num_inference_steps | integer | No | 28 | Number of denoising steps. Higher values improve quality but increase processing time. Range: 10–100. |
| guidance_scale | number | No | 4 | Controls how closely the model follows the prompt. Higher values increase prompt adherence. Range: 1–20. |
| num_images | integer | No | 1 | Number of images to generate per request. Range: 1–4. |
| enable_safety_checker | boolean | No | true | Enables content safety filtering to block inappropriate outputs. |
| output_format | string | No | png | Output image format. Supported values: png, jpeg, webp. |
| acceleration | string | No | regular | Optimization mode. Supported values: regular, fast, ultra_fast. |
Minimum Request
```json
{
"prompt": "Grandmother knitting by a window, an empty chair by her",
"image_size": "landscape_4_3"
}
```
Full Request (all options)
```json
{
"prompt": "Grandmother knitting by a window, an empty chair by her",
"image_size": "landscape_4_3",
"num_inference_steps": 28,
"guidance_scale": 4,
"num_images": 1,
"enable_safety_checker": true,
"output_format": "png",
"acceleration": "regular"
}
```

**Response**
```json
{
"request_id": "z-image-base_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/z-image-base_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```

**Request Headers**
| Header | Value |
| --- | --- |
| Content-Type | application/json |
| --- | --- |
| Cache-Control | no-cache |
| Ocp-Apim-Subscription-Key | Your API subscription key |

**Response Handling**

Common status codes for Z-Image base generate request.

| Code | Meaning |
| 202 | Accepted — Request queued |
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
Error Responses

Queue system errors and model validation errors.

Queue System Errors
// 402 — Insufficient balance
```json
{
"error": "Insufficient Balance",
"message": "Your wallet does not have enough balance."
}
```
// 400 — Model not found
```json
{
"error": "Model not found",
"message": "Model 'z-image-base' not found or is disabled"
}
```
Error via Status/Webhook
```json
{
"request_id": "z-image-base_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "ERROR",
"model_id": "z-image-base",
"error": "Description of the error",
"output": null
}
```
Retrieving Results

Poll the universal status endpoint to check progress and retrieve results.

Endpoint
```http
GET https://gateway.pixazo.ai/v2/requests/status/{request_id}
Ocp-Apim-Subscription-Key: YOUR_API_KEY
cURL Example
curl -H "Ocp-Apim-Subscription-Key: YOUR_API_KEY" \
"https://gateway.pixazo.ai/v2/requests/status/z-image-base_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
Response (Completed)
{
"request_id": "z-image-base_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "COMPLETED",
"model_id": "z-image-base",
"error": null,
"output": {
"media_url": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/v1/z-image-base_019dxxxx-xxxx/output.ext"
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
| --- | --- | --- |
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

Z-Image Base Text To Image API Pricing
| Resolution | Price (USD) |
| All Resolution | $0.01 |
API 
---

### Category: Video Generation

### GenFlare 2.0 API - AI Video Generation APIs
**Page:** https://www.pixazo.ai/models/genflare


by Baidu

GenFlare 2.0 API, developers can bring images to life with realistic motion, creating engaging video content without traditional video production. The API excels at image-to-video transformation, adding natural movement and animation to still photographs for social media, marketing, and creative applications.

Genflare v2
Image To Video


Image To Video

**Request Code**

**Request Parameters**

**Example Request**

**Response**

**Request Headers**

**Response Handling**

#### Genflare v2 Image To Video API Documentation
`https://gateway.pixazo.ai/baidu-genflare-2-0-api/v1`

**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| --- | --- | --- | --- |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Generate Image To Video - Baidu GenFlare 2.0 APIs

**Request Code**
```http
POST https://gateway.pixazo.ai/baidu-genflare-2-0-api/v1/generateImageToVideo2-5Request
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
```json
{
"request_id": "baidu-genflare-2-0-api_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/baidu-genflare-2-0-api_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
Webhook (Optional)

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Generate Image To Video
| Parameter | Required | Type | Description |
| --- | --- | --- | --- |
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
```json
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

**Response**
```json
{
"request_id": "baidu-genflare-2-0-api_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/baidu-genflare-2-0-api_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```

**Request Headers**
| Header | Value |
| --- | --- |
| Content-Type | application/json |
| --- | --- |
| Cache-Control | no-cache |
| Ocp-Apim-Subscription-Key | YOUR_SUBSCRIPTION_KEY |

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
Error Responses

Queue system errors and model validation errors.

Queue System Errors
// 402 — Insufficient balance
```json
{
"error": "Insufficient Balance",
"message": "Your wallet does not have enough balance."
}
```
// 400 — Model not found
```json
{
"error": "Model not found",
"message": "Model 'baidu-genflare-2-0-api' not found or is disabled"
}
```
Error via Status/Webhook
```json
{
"request_id": "baidu-genflare-2-0-api_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "ERROR",
"model_id": "baidu-genflare-2-0-api",
"error": "Description of the error",
"output": null
}
```
Retrieving Results

Poll the universal status endpoint to check progress and retrieve results.

Endpoint
```http
GET https://gateway.pixazo.ai/v2/requests/status/{request_id}
Ocp-Apim-Subscription-Key: YOUR_API_KEY
cURL Example
curl -H "Ocp-Apim-Subscription-Key: YOUR_API_KEY" \
"https://gateway.pixazo.ai/v2/requests/status/baidu-genflare-2-0-api_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
Response (Completed)
{
"request_id": "baidu-genflare-2-0-api_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "COMPLETED",
"model_id": "baidu-genflare-2-0-api",
"error": null,
"output": {
"media_url": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/v1/baidu-genflare-2-0-api_019dxxxx-xxxx/output.ext"
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
| --- | --- | --- |
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

Genflare v2 Image To Video API Pricing

No data available

API 
---
### Higgsfield API - AI Video Generation APIs
**Page:** https://www.pixazo.ai/models/higgsfield


by Higgsfield

Higgsfield API, developers can transform images into videos and generate motion content from text prompts. The API is designed for social media creators, marketers, and developers who need quick, high-quality video generation without complex production pipelines.

Higgsfield v1
Generate Image
Image to Video


Generate Image

**Request Code**

**Request Parameters**

**Example Request**

**Response**

**Request Headers**

**Response Handling**
Image to Video

#### Higgsfield v1 Generate Image API Documentation
`https://gateway.pixazo.ai/ai-model-api/v1`

**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| --- | --- | --- | --- |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |

#### Generate Soul Request - AI Model API

**Request Code**
```http
POST https://gateway.pixazo.ai/ai-model-api/v1/generateSoul
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY

{
"prompt": "Woman on rooftop",
"soul_style_id": "a5f63c3b-70eb-4979-af5e-98c7ee1e18e8",
"width_and_height": "1536x1152",
"image_reference_type": "image_url",
"image_reference_image_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/model.png"
}
```

**Output**
```json
{
"request_id": "ai-model-api_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/ai-model-api_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
Webhook (Optional)

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Generate Soul Request
| Parameter | Required | Type | Description |
| --- | --- | --- | --- |
| webhook_url | No | string (URI) | The URL endpoint where callback requests will be sent. Must be a valid, accessible URI that can receive POST requests. If provided, `webhook_secret` is also required. |
Example: https://your-domain.com/webhook
| webhook_secret | No | string | Secret key used for verifying callback authenticity. Use this to validate that callbacks are genuinely from Higgsfield. Required if `webhook_url` is provided. |
| --- | --- | --- | --- |
Example: webhook_secret_abc123
| prompt | Yes | string | Text description for image generation. Describes the desired image content, style, and visual characteristics. Be descriptive for better results. |
Example: A serene mountain landscape at sunset with golden light
| width_and_height | Yes | enum string | Desired width and height of output image. Available options: 1152x2048, 2048x1152, 2048x1536, 1536x2048, 1344x2016, 2016x1344, 960x1696, 1536x1536, 1536x1152, 1696x960, 1152x1536, 1088x1632, 1632x1088. |
Example: 1152x2048
| enhance_prompt | No | boolean | Whether to automatically enhance and refine the provided prompt. When true, the system will optimize your prompt for better image generation results. |
Example: true
| style_id | No | string (UUID) | Chosen preset for soul image generation. If null then General soul style is applied. Use /getTextToImageGetSoulStyles to get available style IDs. |
Example: 464ea177-8d40-4940-8d9d-b438bab269c7. Get some other style using the API: https://endpoints.appypie.com/api-details#api=ai-model-api-polling&operation=soul-styles
| style_strength | No | number | Strength of the style application. Range from 0.0 (minimal style) to 1.0 (maximum style) with 0.01 step precision. Higher values create more pronounced artistic effects. |
Example: 0.8
| quality | No | enum string | Output image quality. Available options: 720p, 1080p. Higher quality takes longer to generate but produces better results. |
Example: 1080p
| seed | No | integer | null | Seed for reproducibility. If null then random seed is applied. Must be between 1 and 1,000,000. Using the same seed with identical parameters will produce similar results. |
Example: 500000
| custom_reference_id | No | string (UUID) | null | The ID of a character that has already been created. Use this to generate images in a specific character's style. |
Example: 3c90c3cc-0d44-4b50-8888-8dd25736052a
| custom_reference_strength | No | number | Strength of the custom reference application. Range from 0.0 (minimal effect) to 1.0 (maximum effect) with 0.01 step precision. |
Example: 0.9
| image_reference_type | No | string | Type of the image reference. Must be image_url. Required if image_reference_image_url is provided. |
| --- | --- | --- | --- |
Example: image_url
| image_reference_image_url | No | string (URI) | URL of an image to be used as a source for image generation. Must be a valid URI with length between 1-2083 characters. Required if image_reference_type is provided. |
| --- | --- | --- | --- |
Example: https://example.com/reference-image.jpg
| batch_size | No | enum integer | Number of images to generate in a single batch. Available options: 1, 4. Higher batch sizes take longer but can be more cost-effective. |
Example: 4

**Example Request**
```json
{
"prompt": "Woman on rooftop",
"soul_style_id": "a5f63c3b-70eb-4979-af5e-98c7ee1e18e8",
"width_and_height": "1536x1152",
"image_reference_type": "image_url",
"image_reference_image_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/model.png"
}
```

**Response**
```json
{
"request_id": "ai-model-api_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/ai-model-api_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```

**Request Headers**
| Header | Value |
| --- | --- |
| Content-Type | application/json |
| --- | --- |
| Cache-Control | no-cache |
| Ocp-Apim-Subscription-Key | YOUR_SUBSCRIPTION_KEY |

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
Error Responses

Queue system errors and model validation errors.

Queue System Errors
// 402 — Insufficient balance
```json
{
"error": "Insufficient Balance",
"message": "Your wallet does not have enough balance."
}
```
// 400 — Model not found
```json
{
"error": "Model not found",
"message": "Model 'ai-model-api' not found or is disabled"
}
```
Error via Status/Webhook
```json
{
"request_id": "ai-model-api_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "ERROR",
"model_id": "ai-model-api",
"error": "Description of the error",
"output": null
}
```
Retrieving Results

Poll the universal status endpoint to check progress and retrieve results.

Endpoint
```http
GET https://gateway.pixazo.ai/v2/requests/status/{request_id}
Ocp-Apim-Subscription-Key: YOUR_API_KEY
cURL Example
curl -H "Ocp-Apim-Subscription-Key: YOUR_API_KEY" \
"https://gateway.pixazo.ai/v2/requests/status/ai-model-api_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
Response (Completed)
{
"request_id": "ai-model-api_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "COMPLETED",
"model_id": "ai-model-api",
"error": null,
"output": {
"media_url": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/v1/ai-model-api_019dxxxx-xxxx/output.ext"
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
| --- | --- | --- |
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

Higgsfield v1 Generate Image API Pricing
| Resolution | Price (USD) |
| Per generation | $0.15 |
| Per generation | $0.19 |
| Per generation | $0.25 |
| Per generation | $0.37 |

#### Higgsfield v1 Image to Video API Documentation
`https://gateway.pixazo.ai/ai-model-api/v1`

**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| --- | --- | --- | --- |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |

#### Image To Video Request - AI Model API

**Request Code**
```http
POST https://gateway.pixazo.ai/ai-model-api/v1/generateImageToVideoRequest
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY

{
"model": "dop-lite",
"prompt": "A serene lake with gentle ripples, birds flying overhead, cinematic lighting",
"seed": 123456,
"motions_id": "[MOTION_ID]",
"motions_strength": 0.7,
"input_images": ["https://example.com/images/lake-scene.jpg"],
"enhance_prompt": true
}
```

**Output**
```json
{
"request_id": "ai-model-api_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/ai-model-api_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
Webhook (Optional)

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Image To Video Request
| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| webhook_url | string (URI) | No | — | The URL endpoint where callback requests will be sent. Must be a valid, accessible URI that can receive POST requests. If provided, webhook_secret is also required. Example: https://your-domain.com/webhook |
| webhook_secret | string | No | — | Secret key used for verifying callback authenticity. Use this to validate that callbacks are genuinely from Higgsfield. Required if webhook_url is provided. Example: webhook_secret_abc123 |
| --- | --- | --- | --- | --- |
| model | enum string | Yes | — | The image-to-video model to use for generation. Available options: dop-lite, dop-preview, dop-turbo. Each model offers different quality and speed trade-offs. Example: dop-lite |
| prompt | string | Yes | — | Text description/prompt for video generation. Describes the desired video content, style, and motion. Be descriptive for better results. Example: A peaceful sunset over mountains with clouds moving slowly |
| seed | integer | Yes | — | Random seed for reproducible results. Must be between 1 and 1,000,000. Using the same seed with identical parameters will produce similar results. Example: 500000 |
| motions_id | string (UUID) | Yes | — | Unique identifier for the motion preset. This ID corresponds to predefined motion effects available in the system. You can get motion_id using the API: https://endpoints.appypie.com/api-details#api=ai-model-api-polling&operation=motions |
| motions_strength | number (0-1) | Yes | — | Intensity of the motion effect application. Range from 0.0 (minimal effect) to 1.0 (maximum effect) with 0.01 step precision. Higher values create more pronounced motion. Example: 0.75 |
| input_images | array of strings | Yes | — | Array of image URLs for video generation. Each URL must be a publicly accessible link pointing to a valid image file. Supported formats include JPEG, PNG, WebP. Must contain exactly 1 element for standard generation. Example: ["https://example.com/image1.jpg"] |
| input_images_end | array of strings | No | null | Array of end frame image URLs for Start & End Frame functionality. Each URL must be a publicly accessible link pointing to a valid image file. Supported formats include JPEG, PNG, WebP. Minimum length: 1 element. Enables advanced frame interpolation between start and end images. Example: ["https://example.com/end-frame.jpg"] |
| enhance_prompt | boolean | Yes | — | Whether to automatically enhance and refine the provided prompt. When true, the system will optimize your prompt for better video generation results. Example: true |
| check_nsfw | boolean | No | true | Whether to perform NSFW (Not Safe For Work) content detection. When true, the system will check for inappropriate content and may reject the request if detected. |
Minimum Request
```json
{
"model": "dop-lite",
"prompt": "A serene lake with gentle ripples, birds flying overhead, cinematic lighting",
"seed": 123456,
"motions_id": "[MOTION_ID]",
"motions_strength": 0.7,
"input_images": ["https://example.com/images/lake-scene.jpg"],
"enhance_prompt": true
}
```
Full Request (all options)
```json
{
"webhook_url": "https://your-domain.com/webhook/callback",
"webhook_secret": "your-webhook-secret-key",
"model": "dop-lite",
"prompt": "A serene lake with gentle ripples, birds flying overhead, cinematic lighting",
"seed": 123456,
"motions_id": "[MOTION_ID]",
"motions_strength": 0.7,
"input_images": ["https://example.com/images/lake-scene.jpg"],
"input_images_end": ["https://example.com/images/lake-end-frame.jpg"],
"enhance_prompt": true,
"check_nsfw": true
}
```

**Response**
```json
{
"request_id": "ai-model-api_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/ai-model-api_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```

**Request Headers**
| Header | Value |
| --- | --- |
| Content-Type | application/json |
| --- | --- |
| Cache-Control | no-cache |
| Ocp-Apim-Subscription-Key | Your API subscription key |

**Response Handling**

Common status codes for Image To Video Request.

| Code | Meaning |
| 202 | Accepted — Request queued |
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
Error Responses

Queue system errors and model validation errors.

Queue System Errors
// 402 — Insufficient balance
```json
{
"error": "Insufficient Balance",
"message": "Your wallet does not have enough balance."
}
```
// 400 — Model not found
```json
{
"error": "Model not found",
"message": "Model 'ai-model-api' not found or is disabled"
}
```
Error via Status/Webhook
```json
{
"request_id": "ai-model-api_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "ERROR",
"model_id": "ai-model-api",
"error": "Description of the error",
"output": null
}
```
Retrieving Results

Poll the universal status endpoint to check progress and retrieve results.

Endpoint
```http
GET https://gateway.pixazo.ai/v2/requests/status/{request_id}
Ocp-Apim-Subscription-Key: YOUR_API_KEY
cURL Example
curl -H "Ocp-Apim-Subscription-Key: YOUR_API_KEY" \
"https://gateway.pixazo.ai/v2/requests/status/ai-model-api_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
Response (Completed)
{
"request_id": "ai-model-api_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "COMPLETED",
"model_id": "ai-model-api",
"error": null,
"output": {
"media_url": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/v1/ai-model-api_019dxxxx-xxxx/output.ext"
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
| --- | --- | --- |
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

Higgsfield v1 Image to Video API Pricing
| Resolution | Duration | Price (USD) |
| dop-lite | 5s | $0.135 |
| dop-preview | 5s | $0.573 |
| dop-turbo | 5s | $0.416 |
API 
---
### LTX 2.3 API, LTX 2 Pro API, LTX 2.0 API - AI Video Generation APIs
**Page:** https://www.pixazo.ai/models/ltx


by Lightricks

LTX 2.3 API, users can access LTX-Video and LTX-2 variants for generating high-quality video content from text and images. The API is designed for creators and businesses needing production-ready video output with smooth motion and visual coherence.

LTX v2 Pro
LTX v2 19B
LTX v2
Text To Video


Text To Video

**Request Code**

**Request Parameters**

**Example Request**

**Response**

**Request Headers**

**Response Handling**

#### LTX v2 Pro Text To Video API Documentation
`https://gateway.pixazo.ai/lightricks/v1`

**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| --- | --- | --- | --- |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
LTX V2 Video Generate - LTX V2 Pro

**Request Code**
```http
POST https://gateway.pixazo.ai/lightricks/v1/ltx/generate HTTP/1.1

Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY

{
"prompt": "A cinematic drone shot of a futuristic city at night with flying cars and neon lights",
"duration": 10,
"resolution": "1080p",
"generate_audio": true
}
```

**Output**
```json
{
"request_id": "lightricks-video_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/lightricks-video_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
Webhook (Optional)

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - LTX V2 Video Generate
| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| prompt | string | Yes | — | Text description of the video you want to generate. Be specific and descriptive for best results. |
| image | string | No | — | Image URL for image-to-video mode. Must be publicly accessible (HTTPS recommended). |
| duration | integer | No | 6 | Video duration in seconds. Valid values: 6, 8, 10 |
| resolution | string | No | 1080p | Output resolution. Valid values: 1080p, 1440p, 2160p |
| generate_audio | boolean | No | true | Generate audio for the video (audio is preview quality) |
| webhook | string | No | — | Callback URL for completion notification. |
| webhook_events_filter | array | No | ["*"] | Events that trigger webhook. Valid values: ["*"] (all), ["completed"] (success/failure only) |
Minimum Request
```json
{
"prompt": "A serene mountain landscape at sunset with clouds moving slowly across the sky"
}
```
Full Request (all options)
```json
{
"prompt": "A cinematic drone shot of a futuristic city at night with flying cars and neon lights",
"image": "https://example.com/input-photo.jpg",
"duration": 10,
"resolution": "2160p",
"generate_audio": true,
"webhook": "https://yourdomain.com/webhook",
"webhook_events_filter": [
"*"
]
}
```

**Response**
```json
{
"request_id": "lightricks-video_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/lightricks-video_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```

**Request Headers**
| Header | Value |
| --- | --- |
| Content-Type | application/json |
| --- | --- |
| Cache-Control | no-cache |
| Ocp-Apim-Subscription-Key | Your API subscription key |

**Response Handling**

Common status codes for LTX V2 Video Generate.

| Code | Meaning |
| 202 | Accepted — Request queued |
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
Error Responses

Queue system errors and model validation errors.

Queue System Errors
// 402 — Insufficient balance
```json
{
"error": "Insufficient Balance",
"message": "Your wallet does not have enough balance."
}
```
// 400 — Model not found
```json
{
"error": "Model not found",
"message": "Model 'lightricks-video' not found or is disabled"
}
```
Error via Status/Webhook
```json
{
"request_id": "lightricks-video_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "ERROR",
"model_id": "lightricks-video",
"error": "Description of the error",
"output": null
}
```
Retrieving Results

Poll the universal status endpoint to check progress and retrieve results.

Endpoint
```http
GET https://gateway.pixazo.ai/v2/requests/status/{request_id}
Ocp-Apim-Subscription-Key: YOUR_API_KEY
cURL Example
curl -H "Ocp-Apim-Subscription-Key: YOUR_API_KEY" \
"https://gateway.pixazo.ai/v2/requests/status/lightricks-video_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
Response (Completed)
{
"request_id": "lightricks-video_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "COMPLETED",
"model_id": "lightricks-video",
"error": null,
"output": {
"media_url": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/v1/lightricks-video_019dxxxx-xxxx/output.ext"
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
| --- | --- | --- |
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

LTX v2 Pro Text To Video API Pricing

No data available

2. LTX v2 19B

#### LTX v2 19B Image To Video API Documentation
`https://gateway.pixazo.ai/ltx-2-19b-api-513/v1`

**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| --- | --- | --- | --- |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |

#### LTX-2 19B API generate request - LTX-2 19B API

**Request Code**
```http
POST https://gateway.pixazo.ai/ltx-2-19b-api-513/v1/ltx-2-19b-api-request
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY

{
"prompt": "A golden retriever running through a sunlit forest, tail wagging, leaves blowing in the wind",
"image_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/nano-banana.jpeg",
"num_frames": 121,
"video_size": "auto",
"generate_audio": true,
"use_multiscale": true,
"fps": 25,
"acceleration": "none",
"camera_lora": "none",
"camera_lora_scale": 1,
"negative_prompt": "blurry, out of focus, overexposed, underexposed, low contrast, washed out colors, excessive noise, grainy texture, poor lighting, flickering, motion blur, distorted proportions, unnatural skin tones, deformed facial features, asymmetrical face, missing facial features, extra limbs, disfigured hands, wrong hand count, artifacts around text, inconsistent perspective, camera shake, incorrect depth of field, background too sharp, background clutter, distracting reflections, harsh shadows, inconsistent lighting direction, color banding, cartoonish rendering, 3D CGI look, unrealistic materials, uncanny valley effect, incorrect ethnicity, wrong gender, exaggerated expressions, wrong gaze direction, mismatched lip sync, silent or muted audio, distorted voice, robotic voice, echo, background noise, off-sync audio, incorrect dialogue, added dialogue, repetitive speech, jittery movement, awkward pauses, incorrect timing, unnatural transitions, inconsistent framing, tilted camera, flat lighting, inconsistent tone, cinematic oversaturation, stylized filters, or AI artifacts.",
"enable_safety_checker": true,
"video_output_type": "X264 (.mp4)",
"video_quality": "high",
"video_write_mode": "balanced"
}
```

**Output**
```json
{
"request_id": "ltx-2-19b-api-513_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/ltx-2-19b-api-513_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
Webhook (Optional)

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - LTX-2 19B API generate request
| Parameter | Required | Type | Description |
| --- | --- | --- | --- |
| prompt | Yes | string | Text description guiding video generation, including actions, style, and context. |
| image_url | Yes | string | Publicly accessible URL of the input image to animate. Must be reachable by the server. |
| num_frames | No | integer | Total number of frames to generate in the output video. Higher values produce smoother animations. |
| video_size | No | string | Resolution of the output video. Use "auto" to derive from input image dimensions, or specify "512x512", "768x768", etc. |
| generate_audio | No | boolean | Whether to generate synchronized audio based on the prompt. If enabled, audio will be embedded in the video. |
| use_multiscale | No | boolean | Enables multi-scale motion generation for more natural and detailed motion across different spatial scales. |
| fps | No | integer | Frames per second for the output video. Higher values result in smoother motion. |
| acceleration | No | string | Hardware acceleration mode. Use "none" for CPU-only, or "cuda" for GPU acceleration if supported. |
| camera_lora | No | string | Camera motion control via LoRA adapter. Use "none" for no camera motion, or specify a trained LoRA model name. |
| camera_lora_scale | No | number | Strength of the camera motion LoRA effect (0.0 to 2.0). Values above 1.0 amplify motion. |
| negative_prompt | No | string | Description of undesired elements to exclude from the output (e.g., artifacts, distortions). Enhances output quality. |
| enable_safety_checker | No | boolean | Enables content safety filtering to block potentially inappropriate or harmful outputs. |
| video_output_type | No | string | Format of the output video file. Supports "X264 (.mp4)", "H265 (.mp4)", or "WebM". |
| video_quality | No | string | Output video quality level. Values: "low", "balanced", "high". Higher quality increases file size and processing time. |
| video_write_mode | No | string | Strategy for writing video frames. Options: "fast", "balanced", "high-quality". Influences rendering consistency and file integrity. |

**Example Request**
```json
{
"prompt": "A golden retriever running through a sunlit forest, tail wagging, leaves blowing in the wind",
"image_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/nano-banana.jpeg",
"num_frames": 121,
"video_size": "auto",
"generate_audio": true,
"use_multiscale": true,
"fps": 25,
"acceleration": "none",
"camera_lora": "none",
"camera_lora_scale": 1,
"negative_prompt": "blurry, out of focus, overexposed, underexposed, low contrast, washed out colors, excessive noise, grainy texture, poor lighting, flickering, motion blur, distorted proportions, unnatural skin tones, deformed facial features, asymmetrical face, missing facial features, extra limbs, disfigured hands, wrong hand count, artifacts around text, inconsistent perspective, camera shake, incorrect depth of field, background too sharp, background clutter, distracting reflections, harsh shadows, inconsistent lighting direction, color banding, cartoonish rendering, 3D CGI look, unrealistic materials, uncanny valley effect, incorrect ethnicity, wrong gender, exaggerated expressions, wrong gaze direction, mismatched lip sync, silent or muted audio, distorted voice, robotic voice, echo, background noise, off-sync audio, incorrect dialogue, added dialogue, repetitive speech, jittery movement, awkward pauses, incorrect timing, unnatural transitions, inconsistent framing, tilted camera, flat lighting, inconsistent tone, cinematic oversaturation, stylized filters, or AI artifacts.",
"enable_safety_checker": true,
"video_output_type": "X264 (.mp4)",
"video_quality": "high",
"video_write_mode": "balanced"
}
```

**Response**
```json
{
"request_id": "ltx-2-19b-api-513_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/ltx-2-19b-api-513_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```

**Request Headers**
| Header | Value |
| --- | --- |
| Content-Type | application/json |
| --- | --- |
| Cache-Control | no-cache |
| Ocp-Apim-Subscription-Key | YOUR_SUBSCRIPTION_KEY |

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
Error Responses

Queue system errors and model validation errors.

Queue System Errors
// 402 — Insufficient balance
```json
{
"error": "Insufficient Balance",
"message": "Your wallet does not have enough balance."
}
```
// 400 — Model not found
```json
{
"error": "Model not found",
"message": "Model 'ltx-2-19b-api-513' not found or is disabled"
}
```
Error via Status/Webhook
```json
{
"request_id": "ltx-2-19b-api-513_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "ERROR",
"model_id": "ltx-2-19b-api-513",
"error": "Description of the error",
"output": null
}
```
Retrieving Results

Poll the universal status endpoint to check progress and retrieve results.

Endpoint
```http
GET https://gateway.pixazo.ai/v2/requests/status/{request_id}
Ocp-Apim-Subscription-Key: YOUR_API_KEY
cURL Example
curl -H "Ocp-Apim-Subscription-Key: YOUR_API_KEY" \
"https://gateway.pixazo.ai/v2/requests/status/ltx-2-19b-api-513_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
Response (Completed)
{
"request_id": "ltx-2-19b-api-513_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "COMPLETED",
"model_id": "ltx-2-19b-api-513",
"error": null,
"output": {
"media_url": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/v1/ltx-2-19b-api-513_019dxxxx-xxxx/output.ext"
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
| --- | --- | --- |
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

LTX v2 19B Image To Video API Pricing
| Resolution | Price (USD) |
| All Resolution | $0.0896 |
3. LTX v2

#### LTX v2 Image To Video API Documentation
`https://gateway.pixazo.ai/ltx-2-video-api-581/v1`

**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| --- | --- | --- | --- |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |

#### LTX-2 Video API generate request - LTX-2 Video API

**Request Code**
```http
POST https://gateway.pixazo.ai/ltx-2-video-api-581/v1/ltx-2-video-api-request
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY

{
"image_url": "https://storage.googleapis.com/falserverless/example_inputs/ltxv-2-i2v-input.jpg",
"prompt": "A woman stands still amid a busy neon-lit street at night. The camera slowly dollies in toward her face as people blur past, their motion emphasizing her calm presence. City lights flicker and reflections shift across her denim jacket."
}
```

**Output**
```json
{
"request_id": "ltx-2-video-api-581_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/ltx-2-video-api-581_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
Webhook (Optional)

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - LTX-2 Video API generate request
| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| image_url | string | Yes | — | Publicly accessible URL to the input image (JPEG/PNG). Must be reachable by the service. |
| prompt | string | Yes | — | Detailed textual description of the desired motion, camera movement, and visual context for the generated video. |
| duration | integer | No | 6 | Duration of the generated video in seconds. Accepted values: 6, 8, 10. |
| resolution | string | No | 1080p | Output video resolution. Accepted values: "720p", "1080p", "2160p". |
| fps | integer | No | 25 | Frames per second of the output video. Accepted values: 24, 25, 30. |
| generate_audio | boolean | No | false | Whether to generate synchronized audio that matches the video motion and mood. |
Minimum Request
```json
{
"image_url": "https://storage.googleapis.com/falserverless/example_inputs/ltxv-2-i2v-input.jpg",
"prompt": "A woman stands still amid a busy neon-lit street at night. The camera slowly dollies in toward her face as people blur past, their motion emphasizing her calm presence. City lights flicker and reflections shift across her denim jacket."
}
```
Full Request (all options)
```json
{
"image_url": "https://storage.googleapis.com/falserverless/example_inputs/ltxv-2-i2v-input.jpg",
"prompt": "A woman stands still amid a busy neon-lit street at night. The camera slowly dollies in toward her face as people blur past, their motion emphasizing her calm presence. City lights flicker and reflections shift across her denim jacket.",
"duration": 6,
"resolution": "1080p",
"fps": 25,
"generate_audio": true
}
```

**Response**
```json
{
"request_id": "ltx-2-video-api-581_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/ltx-2-video-api-581_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```

**Request Headers**
| Header | Value |
| --- | --- |
| Content-Type | application/json |
| --- | --- |
| Cache-Control | no-cache |
| Ocp-Apim-Subscription-Key | Your API subscription key |

**Response Handling**

Common status codes for LTX-2 Video API generate request.

| Code | Meaning |
| 202 | Accepted — Request queued |
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
Error Responses

Queue system errors and model validation errors.

Queue System Errors
// 402 — Insufficient balance
```json
{
"error": "Insufficient Balance",
"message": "Your wallet does not have enough balance."
}
```
// 400 — Model not found
```json
{
"error": "Model not found",
"message": "Model 'ltx-2-video-api-581' not found or is disabled"
}
```
Error via Status/Webhook
```json
{
"request_id": "ltx-2-video-api-581_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "ERROR",
"model_id": "ltx-2-video-api-581",
"error": "Description of the error",
"output": null
}
```
Retrieving Results

Poll the universal status endpoint to check progress and retrieve results.

Endpoint
```http
GET https://gateway.pixazo.ai/v2/requests/status/{request_id}
Ocp-Apim-Subscription-Key: YOUR_API_KEY
cURL Example
curl -H "Ocp-Apim-Subscription-Key: YOUR_API_KEY" \
"https://gateway.pixazo.ai/v2/requests/status/ltx-2-video-api-581_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
Response (Completed)
{
"request_id": "ltx-2-video-api-581_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "COMPLETED",
"model_id": "ltx-2-video-api-581",
"error": null,
"output": {
"media_url": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/v1/ltx-2-video-api-581_019dxxxx-xxxx/output.ext"
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
| --- | --- | --- |
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

LTX v2 Image To Video API Pricing
| Resolution | Duration | Price (USD) |
| 1080p | 1s | $0.06 per second |
| 1440p | 1s | $0.12 per second |
| 2160p | 1s | $0.24 per second |
API 
---
### Lucy Edit API - AI Video Editing APIs
**Page:** https://www.pixazo.ai/models/lucy-edit


by Decart

Lucy Edit API, developers can implement intelligent video editing features that understand context and intent. The API supports fast video modifications, making it ideal for content creators needing quick turnaround on video edits.

Lucy Edit Fast
Edit Video(Fast)


Edit Video(Fast)

**Request Code**

**Request Parameters**

**Example Request**

**Response**

**Request Headers**

**Response Handling**

#### Lucy Edit Fast Edit Video(Fast) API Documentation
`https://gateway.pixazo.ai/decart-lucy-edit-video-fast-142/v1`

**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| --- | --- | --- | --- |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |

#### Generate Request - Decart Lucy Edit video fast API

**Request Code**
```http
POST https://gateway.pixazo.ai/decart-lucy-edit-video-fast-142/v1/decart-lucy-edit-video-fast-request
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY

{
"prompt": "Change her blue coat to a formal brown jacket",
"video_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/byteplus-videos/1764230857730-80dck2zr09t.mp4",
"enhance_prompt": true
}
```

**Output**
```json
{
"request_id": "decart-lucy-edit-video-fast-142_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/decart-lucy-edit-video-fast-142_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
Webhook (Optional)

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Generate Request
| Parameter | Required | Type | Description |
| --- | --- | --- | --- |
| prompt | Yes | string | A natural language instruction describing the desired modification (e.g., “Change her blue coat to a formal brown jacket”). Be specific about object location, color, style, or context for best results. |
| video_url | Yes | string | A publicly accessible HTTPS URL pointing to the source video file. Supported formats include MP4, MOV, and AVI. The video must be reachable without authentication. |
| enhance_prompt | No | boolean | Enables semantic enhancement of the input prompt using AI to improve clarity and editing accuracy. Recommended for ambiguous or brief prompts. |

**Example Request**
```json
{
"prompt": "Change her blue coat to a formal brown jacket",
"video_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/byteplus-videos/1764230857730-80dck2zr09t.mp4",
"enhance_prompt": true
}
```

**Response**
```json
{
"request_id": "decart-lucy-edit-video-fast-142_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/decart-lucy-edit-video-fast-142_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```

**Request Headers**
| Header | Value |
| --- | --- |
| Content-Type | application/json |
| --- | --- |
| Cache-Control | no-cache |
| Ocp-Apim-Subscription-Key | YOUR_SUBSCRIPTION_KEY |

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
Error Responses

Queue system errors and model validation errors.

Queue System Errors
// 402 — Insufficient balance
```json
{
"error": "Insufficient Balance",
"message": "Your wallet does not have enough balance."
}
```
// 400 — Model not found
```json
{
"error": "Model not found",
"message": "Model 'decart-lucy-edit-video-fast-142' not found or is disabled"
}
```
Error via Status/Webhook
```json
{
"request_id": "decart-lucy-edit-video-fast-142_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "ERROR",
"model_id": "decart-lucy-edit-video-fast-142",
"error": "Description of the error",
"output": null
}
```
Retrieving Results

Poll the universal status endpoint to check progress and retrieve results.

Endpoint
```http
GET https://gateway.pixazo.ai/v2/requests/status/{request_id}
Ocp-Apim-Subscription-Key: YOUR_API_KEY
cURL Example
curl -H "Ocp-Apim-Subscription-Key: YOUR_API_KEY" \
"https://gateway.pixazo.ai/v2/requests/status/decart-lucy-edit-video-fast-142_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
Response (Completed)
{
"request_id": "decart-lucy-edit-video-fast-142_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "COMPLETED",
"model_id": "decart-lucy-edit-video-fast-142",
"error": null,
"output": {
"media_url": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/v1/decart-lucy-edit-video-fast-142_019dxxxx-xxxx/output.ext"
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
| --- | --- | --- |
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

Lucy Edit Fast Edit Video(Fast) API Pricing
| Resolution | Price (USD) |
| All Resolution | $0.2 |
API 
---
### Mochi 1.0 API - AI Video Generation APIs
**Page:** https://www.pixazo.ai/models/mochi


by Mochi

Mochi 1.0 API, developers can generate videos from text descriptions with high visual fidelity and temporal coherence. The API is optimized for creating engaging video content suitable for social media, marketing, and creative storytelling applications.

Mochi v1
Text To Video


Text To Video

**Request Code**

**Request Parameters**

**Example Request**

**Response**

**Request Headers**

**Response Handling**

#### Mochi v1 Text To Video API Documentation
`https://gateway.pixazo.ai/68a5784c0828c041ba519ca6/v1`

**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| --- | --- | --- | --- |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |

#### Video Request Submit - Mochi-v1 API

**Request Code**
```http
POST https://gateway.pixazo.ai/mochi-v1-clone/generate
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY

{
"prompt": "Create a serene video scene of a sparrow bird flying through a lush green forest under a bright blue sky.",
"seed": 445
}
```

**Output**
```json
{
"request_id": "68a5784c0828c041ba519ca6_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/68a5784c0828c041ba519ca6_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
Webhook (Optional)

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Video Request Submit
| Parameter | Required | Type | Description |
| --- | --- | --- | --- |
| prompt | Yes | string | The instruction or description for the video scene to be generated. |
| seed | No | int | A random seed to ensure reproducibility of results. Must be between 1 and 4,294,967,296. |

**Example Request**
```json
{
"prompt": "Create a serene video scene of a sparrow bird flying through a lush green forest under a bright blue sky.",
"seed": 445
}
```

**Response**
```json
{
"request_id": "68a5784c0828c041ba519ca6_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/68a5784c0828c041ba519ca6_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```

**Request Headers**
| Header | Value |
| --- | --- |
| Content-Type | application/json |
| --- | --- |
| Cache-Control | no-cache |
| Ocp-Apim-Subscription-Key | YOUR_SUBSCRIPTION_KEY |
Retrieving Video Status and URL

After submitting your request, use this endpoint to check status and retrieve results.

Endpoint

```http
POST https://gateway.pixazo.ai/mochi-v1-polling-clone/getStatus

Request Body
{
"requestId": "ed9afcxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```

**Request Code**
```http
POST https://gateway.pixazo.ai/mochi-v1-polling-clone/getStatus
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY

{
"requestId": "ed9afcxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```

**Response**
```json
{
"status": "completed",
"video_url": ""
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
Error Responses

Queue system errors and model validation errors.

Queue System Errors
// 402 — Insufficient balance
```json
{
"error": "Insufficient Balance",
"message": "Your wallet does not have enough balance."
}
```
// 400 — Model not found
```json
{
"error": "Model not found",
"message": "Model '68a5784c0828c041ba519ca6' not found or is disabled"
}
```
Error via Status/Webhook
```json
{
"request_id": "68a5784c0828c041ba519ca6_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "ERROR",
"model_id": "68a5784c0828c041ba519ca6",
"error": "Description of the error",
"output": null
}
```
Retrieving Results

Poll the universal status endpoint to check progress and retrieve results.

Endpoint
```http
GET https://gateway.pixazo.ai/v2/requests/status/{request_id}
Ocp-Apim-Subscription-Key: YOUR_API_KEY
cURL Example
curl -H "Ocp-Apim-Subscription-Key: YOUR_API_KEY" \
"https://gateway.pixazo.ai/v2/requests/status/68a5784c0828c041ba519ca6_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
Response (Completed)
{
"request_id": "68a5784c0828c041ba519ca6_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "COMPLETED",
"model_id": "68a5784c0828c041ba519ca6",
"error": null,
"output": {
"media_url": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/v1/68a5784c0828c041ba519ca6_019dxxxx-xxxx/output.ext"
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
| --- | --- | --- |
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

Mochi v1 Text To Video API Pricing
| Resolution | Price (USD) |
| Per generation | $0.4 |
API 
---
### PixVerse V6 API, PixVerse V5.6 API - AI Video Generation APIs
**Page:** https://www.pixazo.ai/models/pixverse


by Pixverse

PixVerse V6 API, developers can generate videos from text and images with focus on visual appeal and motion quality. The API is optimized for social media and marketing use cases, producing videos that capture attention and drive engagement across digital platforms.

View in Playground
Pixverse v6
Pixverse v5.6
Image To Video


Image To Video

**Request Code**

**Request Parameters**

**Example Request**

**Response**

**Request Headers**

**Response Handling**

#### Pixverse v6 Image To Video API Documentation
`https://gateway.pixazo.ai/pixverse-v6-image-to-video/v1`

**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| --- | --- | --- | --- |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
PixVerse V6 Image to Video generate request - PixVerse V6 Image to Video

**Request Code**
POST /pixverse-v6-image-to-video-request HTTP/1.1
Host: gateway.pixazo.ai
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY

```json
{
"prompt": "A gentle snowfall begins around the snow-covered tree, with soft flakes drifting down while the branches sway slightly in the winter breeze. The camera slowly orbits the tree, cinematic, peaceful winter atmosphere.",
"resolution": "720p",
"duration": 5,
"image_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/f1.png"
}
```

**Output**
```json
{
"request_id": "pixverse-v6-image-to-video_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/pixverse-v6-image-to-video_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
Webhook (Optional)

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - PixVerse V6 Image to Video generate request
| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| prompt | string | Yes | — | A detailed text description of the desired video motion and atmosphere. Include camera movement, lighting, and mood. |
| resolution | string | No | 720p | Output video resolution. Supported values: `360p`, `480p`, `720p`, `1080p`. |
| duration | integer | No | 5 | Duration of the generated video in seconds. Must be between 1 and 15 inclusive. |
| negative_prompt | string | No | — | Describes undesired elements to exclude from the output. Helps refine quality and avoid artifacts. |
| image_url | string | Yes | — | Publicly accessible URL of the input image. Must be a valid HTTP/HTTPS link to a JPEG, PNG, or WebP image. |
| style | string | No | — | The style of the generated video. Supported values: anime, 3d_animation, clay, comic, cyberpunk |
| seed | integer | No | — | Random seed for reproducible generation. Same seed + same prompt = same output |
| generate_audio_switch | boolean | No | false | Enable audio generation (BGM, SFX, dialogue). Increases cost per second |
| generate_multi_clip_switch | boolean | No | false | Enable multi-clip generation with dynamic camera changes |
| thinking_type | string | No | auto | Prompt optimization mode. Supported values: enabled (optimize), disabled (turn off), auto (model decision) |
Minimum Request
```json
{
"prompt": "A gentle snowfall begins around the snow-covered tree, with soft flakes drifting down while the branches sway slightly in the winter breeze. The camera slowly orbits the tree, cinematic, peaceful winter atmosphere.",
"resolution": "720p",
"duration": 5,
"image_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/f1.png"
}
```
Full Request (all options)
```json
{
"prompt": "A gentle snowfall begins around the snow-covered tree, with soft flakes drifting down while the branches sway slightly in the winter breeze. The camera slowly orbits the tree, cinematic, peaceful winter atmosphere.",
"resolution": "720p",
"duration": 5,
"negative_prompt": "blurry, low quality, low resolution, pixelated, noisy, grainy, out of focus, poorly lit, poorly exposed, poorly composed, poorly framed, poorly cropped, poorly color corrected, poorly color graded",
"image_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/f1.png"
}
```

**Response**
```json
{
"request_id": "pixverse-v6-image-to-video_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/pixverse-v6-image-to-video_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```

**Request Headers**
| Header | Value |
| --- | --- |
| Content-Type | application/json |
| --- | --- |
| Cache-Control | no-cache |
| Ocp-Apim-Subscription-Key | YOUR_SUBSCRIPTION_KEY |

**Response Handling**

Common status codes for PixVerse V6 Image to Video generate request.

| Code | Meaning |
| 202 | Accepted — Request queued |
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
Error Responses

Queue system errors and model validation errors.

Queue System Errors
// 402 — Insufficient balance
```json
{
"error": "Insufficient Balance",
"message": "Your wallet does not have enough balance."
}
```
// 400 — Model not found
```json
{
"error": "Model not found",
"message": "Model 'pixverse-v6-image-to-video' not found or is disabled"
}
```
Error via Status/Webhook
```json
{
"request_id": "pixverse-v6-image-to-video_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "ERROR",
"model_id": "pixverse-v6-image-to-video",
"error": "Description of the error",
"output": null
}
```
Retrieving Results

Poll the universal status endpoint to check progress and retrieve results.

Endpoint
```http
GET https://gateway.pixazo.ai/v2/requests/status/{request_id}
Ocp-Apim-Subscription-Key: YOUR_API_KEY
cURL Example
curl -H "Ocp-Apim-Subscription-Key: YOUR_API_KEY" \
"https://gateway.pixazo.ai/v2/requests/status/pixverse-v6-image-to-video_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
Response (Completed)
{
"request_id": "pixverse-v6-image-to-video_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "COMPLETED",
"model_id": "pixverse-v6-image-to-video",
"error": null,
"output": {
"media_url": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/v1/pixverse-v6-image-to-video_019dxxxx-xxxx/output.ext"
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
| --- | --- | --- |
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

Pixverse v6 Image To Video API Pricing
| Resolution | Price (USD) |
| 360p | $0.025 per second |
| 360p | $0.035 per second |
| 540p | $0.035 per second |
| 540p | $0.045 per second |
| 720p | $0.045 per second |
| 720p | $0.06 per second |
| 1080p | $0.09 per second |
| 1080p | $0.115 per second |
2. Pixverse v5.6

#### Pixverse v5.6 Text To Video API Documentation
`https://gateway.pixazo.ai/pixverse/v1`

**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| --- | --- | --- | --- |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Pixverse generate request - Pixverse

**Request Code**
POST /pixverse-request HTTP/1.1
Host: gateway.pixazo.ai
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY

```json
{
"prompt": "A golden sunset timelapse over a coastal city, waves crashing against the pier, seagulls flying, warm cinematic lighting, aerial drone perspective",
"aspect_ratio": "16:9",
"resolution": "720p",
"duration": 5
}
```

**Output**
```json
{
"request_id": "pixverse_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/pixverse_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
Webhook (Optional)

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Pixverse generate request
| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| prompt | string | Yes | — | Detailed text description of the desired video scene, including motion, lighting, and style |
| aspect_ratio | string | Yes | — | Aspect ratio of the output video. Supported values: "16:9", "9:16", "1:1", "4:3", "3:4" |
| resolution | string | Yes | — | Output video resolution. Supported values: "720p", "1080p", "2160p" |
| duration | integer | Yes | — | Duration of the video in seconds. Supported values: 3, 5, 7, 10 |
| negative_prompt | string | No | — | Descriptions of elements to avoid in the generated video. Helps refine output quality |
Minimum Request
```json
{
"prompt": "A golden sunset timelapse over a coastal city, waves crashing against the pier, seagulls flying, warm cinematic lighting, aerial drone perspective",
"aspect_ratio": "16:9",
"resolution": "720p",
"duration": 5
}
```
Full Request (all options)
```json
{
"prompt": "A golden sunset timelapse over a coastal city, waves crashing against the pier, seagulls flying, warm cinematic lighting, aerial drone perspective",
"aspect_ratio": "16:9",
"resolution": "720p",
"duration": 5,
"negative_prompt": "blurry, low quality, low resolution, pixelated, noisy, grainy, out of focus, poorly lit, poorly exposed, poorly composed, poorly framed, poorly cropped, poorly color corrected, poorly color graded"
}
```

**Response**
```json
{
"request_id": "pixverse_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/pixverse_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```

**Request Headers**
| Header | Value |
| --- | --- |
| Content-Type | application/json |
| --- | --- |
| Cache-Control | no-cache |
| Ocp-Apim-Subscription-Key | Your API subscription key |

**Response Handling**

Common status codes for Pixverse generate request.

| Code | Meaning |
| 202 | Accepted — Request queued |
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
Error Responses

Queue system errors and model validation errors.

Queue System Errors
// 402 — Insufficient balance
```json
{
"error": "Insufficient Balance",
"message": "Your wallet does not have enough balance."
}
```
// 400 — Model not found
```json
{
"error": "Model not found",
"message": "Model 'pixverse' not found or is disabled"
}
```
Error via Status/Webhook
```json
{
"request_id": "pixverse_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "ERROR",
"model_id": "pixverse",
"error": "Description of the error",
"output": null
}
```
Retrieving Results

Poll the universal status endpoint to check progress and retrieve results.

Endpoint
```http
GET https://gateway.pixazo.ai/v2/requests/status/{request_id}
Ocp-Apim-Subscription-Key: YOUR_API_KEY
cURL Example
curl -H "Ocp-Apim-Subscription-Key: YOUR_API_KEY" \
"https://gateway.pixazo.ai/v2/requests/status/pixverse_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
Response (Completed)
{
"request_id": "pixverse_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "COMPLETED",
"model_id": "pixverse",
"error": null,
"output": {
"media_url": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/v1/pixverse_019dxxxx-xxxx/output.ext"
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
| --- | --- | --- |
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

Pixverse v5.6 Text To Video API Pricing

No data available


#### Pixverse v5.6 Image To Video API Documentation
`https://gateway.pixazo.ai/pixverse-i2v/v1`

**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| --- | --- | --- | --- |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Pixverse i2v generate request - Pixverse i2v

**Request Code**
```http
POST https://gateway.pixazo.ai/pixverse-i2v/v1/pixverse-i2v-request
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY

{
"prompt": "A fierce dragon breathing fire across a stormy night sky, lightning flashing in the background, cinematic dark fantasy",
"resolution": "720p",
"duration": "5",
"image_url": "https://imagesai.appypie.com/7686410/ZU2sxXLmgRF6dgSktn9o_017731475651251.png"
}
```

**Output**
```json
{
"request_id": "pixverse-i2v_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/pixverse-i2v_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
Webhook (Optional)

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Pixverse i2v generate request
| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| prompt | string | Yes | — | Text description describing the desired video motion and scene. Be detailed for best results. |
| resolution | string | Yes | — | Target video resolution. Supported values: "720p", "1080p". |
| duration | string | Yes | — | Duration of the generated video in seconds. Supported values: "5", "10". |
| negative_prompt | string | No | — | Describes undesired elements to exclude from the video. Helps refine output quality. |
| image_url | string | Yes | — | Publicly accessible URL of the input image to animate. Must be a valid HTTP/HTTPS link. |
Minimum Request
```json
{
"prompt": "A fierce dragon breathing fire across a stormy night sky, lightning flashing in the background, cinematic dark fantasy",
"resolution": "720p",
"duration": "5",
"image_url": "https://imagesai.appypie.com/7686410/ZU2sxXLmgRF6dgSktn9o_017731475651251.png"
}
```
Full Request (all options)
```json
{
"prompt": "A fierce dragon breathing fire across a stormy night sky, lightning flashing in the background, cinematic dark fantasy",
"resolution": "720p",
"duration": "5",
"negative_prompt": "blurry, low quality, low resolution, pixelated, noisy, grainy",
"image_url": "https://imagesai.appypie.com/7686410/ZU2sxXLmgRF6dgSktn9o_017731475651251.png"
}
```

**Response**
```json
{
"request_id": "pixverse-i2v_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/pixverse-i2v_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```

**Request Headers**
| Header | Value |
| --- | --- |
| Content-Type | application/json |
| --- | --- |
| Cache-Control | no-cache |
| Ocp-Apim-Subscription-Key | Your API subscription key |

**Response Handling**

Common status codes for Pixverse i2v generate request.

| Code | Meaning |
| 202 | Accepted — Request queued |
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
Error Responses

Queue system errors and model validation errors.

Queue System Errors
// 402 — Insufficient balance
```json
{
"error": "Insufficient Balance",
"message": "Your wallet does not have enough balance."
}
```
// 400 — Model not found
```json
{
"error": "Model not found",
"message": "Model 'pixverse-i2v' not found or is disabled"
}
```
Error via Status/Webhook
```json
{
"request_id": "pixverse-i2v_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "ERROR",
"model_id": "pixverse-i2v",
"error": "Description of the error",
"output": null
}
```
Retrieving Results

Poll the universal status endpoint to check progress and retrieve results.

Endpoint
```http
GET https://gateway.pixazo.ai/v2/requests/status/{request_id}
Ocp-Apim-Subscription-Key: YOUR_API_KEY
cURL Example
curl -H "Ocp-Apim-Subscription-Key: YOUR_API_KEY" \
"https://gateway.pixazo.ai/v2/requests/status/pixverse-i2v_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
Response (Completed)
{
"request_id": "pixverse-i2v_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "COMPLETED",
"model_id": "pixverse-i2v",
"error": null,
"output": {
"media_url": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/v1/pixverse-i2v_019dxxxx-xxxx/output.ext"
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
| --- | --- | --- |
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

Pixverse v5.6 Image To Video API Pricing

No data available

API 
---
### Topaz API - AI Video Enhancement APIs
**Page:** https://www.pixazo.ai/models/topaz


by Topaz

Topaz API, developers can upscale videos to higher resolutions suitable for broadcast, cinema, and large-format displays. The API leverages Topaz's industry-recognized enhancement algorithms to restore old footage, improve user-generated content, and prepare videos for demanding production workflows where visual quality is critical.

Topaz Video Upscaler
Upscale Video


Upscale Video

**Request Code**

**Request Parameters**

**Example Request**

**Response**

**Request Headers**

**Response Handling**

#### Topaz Video Upscaler Upscale Video API Documentation
`https://gateway.pixazo.ai/topaz-upscale-video-753/v1`

**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| --- | --- | --- | --- |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Topaz Upscale Video generate request - Topaz Upscale Video

**Request Code**
```http
POST https://gateway.pixazo.ai/topaz-upscale-video-753/v1/topaz-upscale-video-request
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY

{
"video_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/kandinsky-5-0-pro-953/ijNirwcnwvZ0VLVPIylDF_output.mp4",
"upscale_factor": 2
}
```

**Output**
```json
{
"request_id": "topaz-upscale-video-753_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/topaz-upscale-video-753_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
Webhook (Optional)

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Topaz Upscale Video generate request
| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| video_url | string | Yes | — | Publicly accessible URL of the source video file to be upscaled. Must be reachable by the API server. |
| upscale_factor | integer | Yes | — | Multiplier for resolution enhancement. Supported values: 2 or 4. A factor of 2 doubles width and height (e.g., 720p → 1440p). A factor of 4 quadruples resolution (e.g., 720p → 2880p). |
Minimum Request
```json
{
"video_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/kandinsky-5-0-pro-953/ijNirwcnwvZ0VLVPIylDF_output.mp4",
"upscale_factor": 2
}
```
Full Request (all options)
```json
{
"video_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/kandinsky-5-0-pro-953/ijNirwcnwvZ0VLVPIylDF_output.mp4",
"upscale_factor": 2
}
```

**Response**
```json
{
"request_id": "topaz-upscale-video-753_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/topaz-upscale-video-753_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```

**Request Headers**
| Header | Value |
| --- | --- |
| Content-Type | application/json |
| --- | --- |
| Cache-Control | no-cache |
| Ocp-Apim-Subscription-Key | Your API subscription key |

**Response Handling**

Common status codes for Topaz Upscale Video generate request.

| Code | Meaning |
| 202 | Accepted — Request queued |
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
Error Responses

Queue system errors and model validation errors.

Queue System Errors
// 402 — Insufficient balance
```json
{
"error": "Insufficient Balance",
"message": "Your wallet does not have enough balance."
}
```
// 400 — Model not found
```json
{
"error": "Model not found",
"message": "Model 'topaz-upscale-video-753' not found or is disabled"
}
```
Error via Status/Webhook
```json
{
"request_id": "topaz-upscale-video-753_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "ERROR",
"model_id": "topaz-upscale-video-753",
"error": "Description of the error",
"output": null
}
```
Retrieving Results

Poll the universal status endpoint to check progress and retrieve results.

Endpoint
```http
GET https://gateway.pixazo.ai/v2/requests/status/{request_id}
Ocp-Apim-Subscription-Key: YOUR_API_KEY
cURL Example
curl -H "Ocp-Apim-Subscription-Key: YOUR_API_KEY" \
"https://gateway.pixazo.ai/v2/requests/status/topaz-upscale-video-753_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
Response (Completed)
{
"request_id": "topaz-upscale-video-753_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "COMPLETED",
"model_id": "topaz-upscale-video-753",
"error": null,
"output": {
"media_url": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/v1/topaz-upscale-video-753_019dxxxx-xxxx/output.ext"
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
| --- | --- | --- |
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

Topaz Video Upscaler Upscale Video API Pricing

No data available

API 
---
### VEED Fabric 1.0 API, Veed API - AI Video Generation APIs
**Page:** https://www.pixazo.ai/models/veed


by Veed

VEED Fabric 1.0 API, developers can implement advanced video processing features that automatically improve video quality and remove backgrounds without green screens. The API is designed for content creators and video platforms needing automated video enhancement at scale.

Veed Fabric v1.0
Veed v1
Fabric Generation


Fabric Generation

**Request Code**

**Request Parameters**

**Example Request**

**Response**

**Request Headers**

**Response Handling**

#### Veed Fabric v1.0 Fabric Generation API Documentation
`https://gateway.pixazo.ai/veed-fabric-1-0-api-130/v1`

**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| --- | --- | --- | --- |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
VEED Fabric 1.0 API generate request - VEED Fabric 1.0

**Request Code**
POST /veed-fabric-1-0-api-request HTTP/1.1
Host: gateway.pixazo.ai
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY

```json
{
"image_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/input_model.png",
"audio_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/Oz_g4AwQvXtXpUHL3Pa7u_Hope.mp3",
"resolution": "720p"
}
```

**Output**
```json
{
"request_id": "veed-fabric-1-0-api-130_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/veed-fabric-1-0-api-130_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
Webhook (Optional)

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - VEED Fabric 1.0 API generate request
| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| image_url | string | Yes | — | URL pointing to a static image (PNG, JPG) to be animated. Must be publicly accessible. |
| audio_url | string | Yes | — | URL pointing to an audio file (MP3, WAV) that will drive lip-sync and facial motion. Must be publicly accessible. |
| resolution | string | No | 720p | Output video resolution. Supported values: "720p", "1080p". |
Minimum Request
```json
{
"image_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/input_model.png",
"audio_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/Oz_g4AwQvXtXpUHL3Pa7u_Hope.mp3"
}
```
Full Request (all options)
```json
{
"image_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/input_model.png",
"audio_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/Oz_g4AwQvXtXpUHL3Pa7u_Hope.mp3",
"resolution": "720p"
}
```

**Response**
```json
{
"request_id": "veed-fabric-1-0-api-130_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/veed-fabric-1-0-api-130_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```

**Request Headers**
| Header | Value |
| --- | --- |
| Content-Type | application/json |
| --- | --- |
| Cache-Control | no-cache |
| Ocp-Apim-Subscription-Key | Your subscription key |

**Response Handling**

Common status codes for VEED Fabric 1.0 API generate request.

| Code | Meaning |
| 202 | Accepted — Request queued |
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
Error Responses

Queue system errors and model validation errors.

Queue System Errors
// 402 — Insufficient balance
```json
{
"error": "Insufficient Balance",
"message": "Your wallet does not have enough balance."
}
```
// 400 — Model not found
```json
{
"error": "Model not found",
"message": "Model 'veed-fabric-1-0-api-130' not found or is disabled"
}
```
Error via Status/Webhook
```json
{
"request_id": "veed-fabric-1-0-api-130_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "ERROR",
"model_id": "veed-fabric-1-0-api-130",
"error": "Description of the error",
"output": null
}
```
Retrieving Results

Poll the universal status endpoint to check progress and retrieve results.

Endpoint
```http
GET https://gateway.pixazo.ai/v2/requests/status/{request_id}
Ocp-Apim-Subscription-Key: YOUR_API_KEY
cURL Example
curl -H "Ocp-Apim-Subscription-Key: YOUR_API_KEY" \
"https://gateway.pixazo.ai/v2/requests/status/veed-fabric-1-0-api-130_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
Response (Completed)
{
"request_id": "veed-fabric-1-0-api-130_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "COMPLETED",
"model_id": "veed-fabric-1-0-api-130",
"error": null,
"output": {
"media_url": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/v1/veed-fabric-1-0-api-130_019dxxxx-xxxx/output.ext"
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
| --- | --- | --- |
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

Veed Fabric v1.0 Fabric Generation API Pricing

No data available

2. Veed v1

#### Veed v1 Video Background Remover API Documentation
`https://gateway.pixazo.ai/veed-video-background-remover-541/v1`

**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| --- | --- | --- | --- |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Veed Video Background Remover generate request - Veed Video Background Remover

**Request Code**
```http
POST https://gateway.pixazo.ai/veed-video-background-remover-541/v1/veed-video-background-remover-request
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY

{
"video_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/kandinsky-5-0-pro-953/ijNirwcnwvZ0VLVPIylDF_output.mp4",
"output_codec": "vp9",
"refine_foreground_edges": true,
"subject_is_person": true
}
```

**Output**
```json
{
"request_id": "veed-video-background-remover-541_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/veed-video-background-remover-541_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
Webhook (Optional)

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Veed Video Background Remover generate request
| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| video_url | string | Yes | — | Publicly accessible URL of the input video file to process. Must be accessible without authentication. |
| output_codec | string | No | vp9 | Encoding codec for the output video. Supported values: vp9, h264, h265. |
| refine_foreground_edges | boolean | No | true | Enables advanced edge refinement for smoother transitions between subject and background. Improves quality but increases processing time. |
| subject_is_person | boolean | No | true | Optimizes segmentation model for human subjects. Disable if processing objects or animals. |
Minimum Request
```json
{
"video_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/kandinsky-5-0-pro-953/ijNirwcnwvZ0VLVPIylDF_output.mp4"
}
```
Full Request (all options)
```json
{
"video_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/kandinsky-5-0-pro-953/ijNirwcnwvZ0VLVPIylDF_output.mp4",
"output_codec": "vp9",
"refine_foreground_edges": true,
"subject_is_person": true
}
```

**Response**
```json
{
"request_id": "veed-video-background-remover-541_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/veed-video-background-remover-541_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```

**Request Headers**
| Header | Value |
| --- | --- |
| Content-Type | application/json |
| --- | --- |
| Cache-Control | no-cache |
| Ocp-Apim-Subscription-Key | Your API subscription key |

**Response Handling**

Common status codes for Veed Video Background Remover generate request.

| Code | Meaning |
| 202 | Accepted — Request queued |
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
Error Responses

Queue system errors and model validation errors.

Queue System Errors
// 402 — Insufficient balance
```json
{
"error": "Insufficient Balance",
"message": "Your wallet does not have enough balance."
}
```
// 400 — Model not found
```json
{
"error": "Model not found",
"message": "Model 'veed-video-background-remover-541' not found or is disabled"
}
```
Error via Status/Webhook
```json
{
"request_id": "veed-video-background-remover-541_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "ERROR",
"model_id": "veed-video-background-remover-541",
"error": "Description of the error",
"output": null
}
```
Retrieving Results

Poll the universal status endpoint to check progress and retrieve results.

Endpoint
```http
GET https://gateway.pixazo.ai/v2/requests/status/{request_id}
Ocp-Apim-Subscription-Key: YOUR_API_KEY
cURL Example
curl -H "Ocp-Apim-Subscription-Key: YOUR_API_KEY" \
"https://gateway.pixazo.ai/v2/requests/status/veed-video-background-remover-541_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
Response (Completed)
{
"request_id": "veed-video-background-remover-541_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "COMPLETED",
"model_id": "veed-video-background-remover-541",
"error": null,
"output": {
"media_url": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/v1/veed-video-background-remover-541_019dxxxx-xxxx/output.ext"
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
| --- | --- | --- |
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

Veed v1 Video Background Remover API Pricing

No data available

API 
---
### Vidu API - AI Video Generation APIs
**Page:** https://www.pixazo.ai/models/vidu


by Vidu

Vidu API, developers can create videos that follow reference images or styles, ensuring brand consistency and creative control. The API's Q2 Pro model delivers high-quality output suitable for commercial video production and content creation workflows.

Vidu Q3
Vidu v1
Text To Video


Text To Video

**Request Code**

**Request Parameters**

**Example Request**

**Response**

**Request Headers**

**Response Handling**

#### Vidu Q3 Text To Video API Documentation
`https://gateway.pixazo.ai/vidu/v1`

**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| --- | --- | --- | --- |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Vidu generate request - Vidu

**Request Code**
```http
POST https://gateway.pixazo.ai/vidu/v1/vidu-request
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY

{
"prompt": "A slow-motion capture of a hummingbird hovering beside a vibrant red hibiscus flower, iridescent feathers catching sunlight, shallow depth of field, garden background",
"duration": 5,
"aspect_ratio": "16:9",
"resolution": "720p",
"audio": true
}
```

**Output**
```json
{
"request_id": "vidu_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/vidu_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
Webhook (Optional)

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Vidu generate request
| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| prompt | string | Yes | — | Detailed text description of the desired video scene |
| duration | integer | Yes | — | Duration of the generated video in seconds (recommended: 3–10) |
| aspect_ratio | string | Yes | — | Video aspect ratio: "16:9", "9:16", "1:1", "4:3", or "3:4" |
| resolution | string | Yes | — | Output resolution: "720p", "1080p", or "4K" |
| audio | boolean | Yes | — | Whether to generate synchronized audio with the video |
Minimum Request
```json
{
"prompt": "A slow-motion capture of a hummingbird hovering beside a vibrant red hibiscus flower, iridescent feathers catching sunlight, shallow depth of field, garden background",
"duration": 5,
"aspect_ratio": "16:9",
"resolution": "720p",
"audio": true
}
```
Full Request (all options)
```json
{
"prompt": "A slow-motion capture of a hummingbird hovering beside a vibrant red hibiscus flower, iridescent feathers catching sunlight, shallow depth of field, garden background",
"duration": 5,
"aspect_ratio": "16:9",
"resolution": "720p",
"audio": true
}
```

**Response**
```json
{
"request_id": "vidu_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/vidu_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```

**Request Headers**
| Header | Value |
| --- | --- |
| Content-Type | application/json |
| --- | --- |
| Cache-Control | no-cache |
| Ocp-Apim-Subscription-Key | Your API subscription key |

**Response Handling**

Common status codes for Vidu generate request.

| Code | Meaning |
| 202 | Accepted — Request queued |
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
Error Responses

Queue system errors and model validation errors.

Queue System Errors
// 402 — Insufficient balance
```json
{
"error": "Insufficient Balance",
"message": "Your wallet does not have enough balance."
}
```
// 400 — Model not found
```json
{
"error": "Model not found",
"message": "Model 'vidu' not found or is disabled"
}
```
Error via Status/Webhook
```json
{
"request_id": "vidu_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "ERROR",
"model_id": "vidu",
"error": "Description of the error",
"output": null
}
```
Retrieving Results

Poll the universal status endpoint to check progress and retrieve results.

Endpoint
```http
GET https://gateway.pixazo.ai/v2/requests/status/{request_id}
Ocp-Apim-Subscription-Key: YOUR_API_KEY
cURL Example
curl -H "Ocp-Apim-Subscription-Key: YOUR_API_KEY" \
"https://gateway.pixazo.ai/v2/requests/status/vidu_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
Response (Completed)
{
"request_id": "vidu_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "COMPLETED",
"model_id": "vidu",
"error": null,
"output": {
"media_url": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/v1/vidu_019dxxxx-xxxx/output.ext"
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
| --- | --- | --- |
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

Vidu Q3 Text To Video API Pricing

No data available

2. Vidu v1

#### Vidu v1 Reference To Video API Documentation
`https://gateway.pixazo.ai/vidu-q2-reference-to-video-pro-api-454/v1`

**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| --- | --- | --- | --- |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |

#### Vidu Q2 Reference to Video Pro API generate request - Vidu Q2 Reference to Video Pro API

**Request Code**
```http
POST https://gateway.pixazo.ai/vidu-q2-reference-to-video-pro-api-454/v1/vidu-q2-reference-to-video-pro-api-request
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY

{
"prompt": "@Figure 1 Character Reference@Refer to the special effects, movements, and camera work of Video 1.",
"reference_image_urls": [
"https://storage.googleapis.com/falserverless/model_tests/video_models/vidu-image-3123041388101890.png"
],
"reference_video_urls": [
"https://storage.googleapis.com/falserverless/model_tests/video_models/vidu-video-3123002003131623.mp4"
],
"duration": 4,
"resolution": "720p",
"aspect_ratio": "16:9",
"movement_amplitude": "auto"
}
```

**Output**
```json
{
"request_id": "vidu-q2-reference-to-video-pro-api-454_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/vidu-q2-reference-to-video-pro-api-454_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
Webhook (Optional)

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Vidu Q2 Reference to Video Pro API generate request
| Parameter | Required | Type | Description |
| --- | --- | --- | --- |
| prompt | Yes | string | A descriptive text prompt that defines the desired action, scene, or context, while referencing the style and motion from the provided reference video. Use `@Figure X Character Reference@` syntax to explicitly link character identities to reference imagery. |
| reference_image_urls | Yes | array of strings | Array of HTTPS URLs pointing to static reference images used to define character appearance, clothing, or static scene context. Each URL must be publicly accessible. |
| reference_video_urls | Yes | array of strings | Array of HTTPS URLs pointing to reference video clips that define motion patterns, camera movement, lighting transitions, and special effects to be replicated. Must be MP4 format. |
| duration | Optional | integer | Duration of the output video in seconds. Must be between 1 and 10. |
| resolution | Optional | string | Output video resolution. Supported values: "480p", "720p", "1080p". |
| aspect_ratio | Optional | string | Output video aspect ratio. Supported values: "16:9", "9:16", "1:1". |
| movement_amplitude | Optional | string | Controls the intensity of motion reproduction from reference video. Values: "low", "medium", "high", "auto". "auto" enables dynamic adaptation based on reference content. |

**Example Request**
```json
{
"prompt": "@Figure 1 Character Reference@Refer to the special effects, movements, and camera work of Video 1.",
"reference_image_urls": [
"https://storage.googleapis.com/falserverless/model_tests/video_models/vidu-image-3123041388101890.png"
],
"reference_video_urls": [
"https://storage.googleapis.com/falserverless/model_tests/video_models/vidu-video-3123002003131623.mp4"
],
"duration": 4,
"resolution": "720p",
"aspect_ratio": "16:9",
"movement_amplitude": "auto"
}
```

**Response**
```json
{
"request_id": "vidu-q2-reference-to-video-pro-api-454_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/vidu-q2-reference-to-video-pro-api-454_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```

**Request Headers**
| Header | Value |
| --- | --- |
| Content-Type | application/json |
| --- | --- |
| Cache-Control | no-cache |
| Ocp-Apim-Subscription-Key | YOUR_SUBSCRIPTION_KEY |

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
Error Responses

Queue system errors and model validation errors.

Queue System Errors
// 402 — Insufficient balance
```json
{
"error": "Insufficient Balance",
"message": "Your wallet does not have enough balance."
}
```
// 400 — Model not found
```json
{
"error": "Model not found",
"message": "Model 'vidu-q2-reference-to-video-pro-api-454' not found or is disabled"
}
```
Error via Status/Webhook
```json
{
"request_id": "vidu-q2-reference-to-video-pro-api-454_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "ERROR",
"model_id": "vidu-q2-reference-to-video-pro-api-454",
"error": "Description of the error",
"output": null
}
```
Retrieving Results

Poll the universal status endpoint to check progress and retrieve results.

Endpoint
```http
GET https://gateway.pixazo.ai/v2/requests/status/{request_id}
Ocp-Apim-Subscription-Key: YOUR_API_KEY
cURL Example
curl -H "Ocp-Apim-Subscription-Key: YOUR_API_KEY" \
"https://gateway.pixazo.ai/v2/requests/status/vidu-q2-reference-to-video-pro-api-454_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
Response (Completed)
{
"request_id": "vidu-q2-reference-to-video-pro-api-454_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "COMPLETED",
"model_id": "vidu-q2-reference-to-video-pro-api-454",
"error": null,
"output": {
"media_url": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/v1/vidu-q2-reference-to-video-pro-api-454_019dxxxx-xxxx/output.ext"
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
| --- | --- | --- |
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

Vidu v1 Reference To Video API Pricing

No data available

API 
---

### Category: Audio & Music

### Ace Step 1.5 XL API, Ace Step 1.5 API - AI Music Generation APIs
**Page:** https://www.pixazo.ai/models/ace-step


by ACE Studio

Ace Step 1.5 XL API, developers can generate high-quality custom soundtracks, background music, and audio content with ease. The API enables creators to produce professional-grade music compositions suitable for videos, games, podcasts, and multimedia projects without traditional music production expertise.

Ace Step 1.5 XL
Ace Step 1.5
Music Generation


Music Generation

**Request Code**

**Request Parameters**

**Example Request**

**Response**

**Request Headers**

**Response Handling**

#### Ace Step 1.5 XL Music Generation API Documentation
`https://gateway.pixazo.ai/ace-step-xl/v1`

**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| --- | --- | --- | --- |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |

#### Submit Music Generation Request - ACE Step 1.5 XL API

**Request Code**
```http
POST https://gateway.pixazo.ai/ace-step-xl/v1/submitMusicGenerationRequest
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY

{
"prompt": "Uplifting pop song with acoustic guitar and bright piano",
"lyrics": "[verse]\nWoke up to a sky painted gold\n\n[chorus]\nYou are my sunshine after the rain",
"duration": 60
}
```

**Output**
```json
{
"request_id": "ace-step-xl_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/ace-step-xl_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
Webhook (Optional)

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Submit Music Generation Request
| Parameter | Required | Type | Description |
| --- | --- | --- | --- |
| prompt | Yes | string | Genre, instruments, mood, vocals description (e.g., "Uplifting pop song with acoustic guitar, bright piano") |
| lyrics | No | string | Song lyrics with structure tags: [verse], [chorus], [bridge], [outro]. Default: empty |
| duration | No | number | Audio duration in seconds, max 600 (10 min). Default: 30 |
| seed | No | integer | Fixed seed for reproducible output. Default: -1 (random) |
| bpm | No | number | Beats per minute. Default: auto |
| key | No | string | Musical key (e.g., "C major", "B minor"). Default: auto |
| time_signature | No | string | Time signature (e.g., "4/4", "3/4"). Default: auto |
| batch_size | No | integer | Number of variations to generate (1-4). Default: 1 |
| thinking | No | boolean | LM "thinks" about prompt before generating for better quality. Default: false |

**Example Request**
```json
{
"prompt": "Uplifting pop song with acoustic guitar, bright piano, and energetic drums. Female vocals, 120 BPM, key of C major",
"lyrics": "[verse]\nWoke up to a sky painted gold\nSoft light dancing on my window\n\n[chorus]\nYou are my sunshine after the rain\nRunning wild through every vein",
"duration": 180,
"seed": 42,
"bpm": 120,
"key": "C major",
"time_signature": "4/4",
"batch_size": 4,
"thinking": true
}
```

**Response**
```json
{
"request_id": "ace-step-xl_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/ace-step-xl_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```

**Request Headers**
| Header | Value |
| --- | --- |
| Content-Type | application/json |
| --- | --- |
| Cache-Control | no-cache |
| Ocp-Apim-Subscription-Key | YOUR_SUBSCRIPTION_KEY |

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
Error Responses

Queue system errors and model validation errors.

Queue System Errors
// 402 — Insufficient balance
```json
{
"error": "Insufficient Balance",
"message": "Your wallet does not have enough balance. Required: $0.01"
}
```
// 400 — Model not found
```json
{
"error": "Model not found",
"message": "Model 'ace-step-xl' not found or is disabled"
}
```
Error via Status/Webhook
```json
{
"request_id": "ace-step-xl_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "ERROR",
"model_id": "ace-step-xl",
"error": "Description of the error",
"output": null
}
```
Retrieving Results

Poll the universal status endpoint to check progress and retrieve results.

Endpoint
```http
GET https://gateway.pixazo.ai/v2/requests/status/{request_id}
Ocp-Apim-Subscription-Key: YOUR_API_KEY
cURL Example
curl -H "Ocp-Apim-Subscription-Key: YOUR_API_KEY" \
"https://gateway.pixazo.ai/v2/requests/status/ace-step-xl_019d42ce-946d-7739-f812-6875c434cb790"
Response (Completed)
{
"request_id": "ace-step-xl_019d42ce-946d-7739-f812-6875c434cb790",
"status": "COMPLETED",
"model_id": "ace-step-xl",
"error": null,
"output": {
"media_url": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/v1/ace-step-xl_019d42ce-946d-7739-f812-6875c434cb790/output_0.wav"
],
"media_type": "audio/wav"
},
"created_at": "2026-03-31T07:32:03.749Z",
"updated_at": "2026-03-31T07:32:20.000Z",
"completed_at": "2026-03-31T07:32:20.000Z"
}
```
Response Fields
| Field | Type | Description |
| --- | --- | --- |
| request_id | string | Unique request identifier |
| status | string | QUEUED, PROCESSING, COMPLETED, FAILED, or ERROR |
| model_id | string | Model that processed the request |
| error | string|null | Error message if failed |
| output.media_url | array | URLs to generated media (R2 CDN) |
| output.media_type | string | MIME type (audio/wav) |
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

Ace Step 1.5 XL Music Generation API Pricing
| Resolution | Price (USD) |
| Per Generation | $0.015 |
2. Ace Step 1.5

#### Ace Step 1.5 Music Generation API Documentation
`https://gateway.pixazo.ai/ace-step/v1`

**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| --- | --- | --- | --- |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Generate Music - Ace step

**Request Code**
```http
POST https://gateway.pixazo.ai/ace-step/v1/generate
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY

{
"prompt": "A cinematic Hans Zimmer style orchestral piece, building tension with heavy percussion and brass, epic atmosphere",
"lyrics": "",
"instrumental": true,
"duration": 120,
"bpm": 140,
"infer_steps": 25,
"guidance_scale": 7.5,
"seed": 42
}
```

**Output**
```json
{
"request_id": "ace-step_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/ace-step_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
Webhook (Optional)

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Generate Music
| Parameter | Required | Type | Description |
| --- | --- | --- | --- |
| prompt | Yes | string | Describes the overall musical style, genre, mood, instrumentation, and atmosphere. |
| lyrics | No | string | Temporal script of the song. Controls structure, sections, vocal style, instrumental breaks, and lyrical content. |
| instrumental | No | boolean | If true, generates instrumental-only music (no vocals). |
| duration | No | integer | Target duration in seconds. |
| bpm | No | integer | Target tempo in beats per minute. |
| infer_steps | No | integer | Number of inference steps; higher values may increase quality but take longer. |
| guidance_scale | No | float | Controls how strongly the model follows the prompt. |
| seed | No | integer | Used for reproducibility. Same seed and parameters produce similar outputs. |

**Example Request**
```json
{
"prompt": "A cinematic Hans Zimmer style orchestral piece, building tension with heavy percussion and brass, epic atmosphere",
"lyrics": "",
"instrumental": true,
"duration": 120,
"bpm": 140,
"infer_steps": 25,
"guidance_scale": 7.5,
"seed": 42
}
```

**Response**
```json
{
"request_id": "ace-step_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/ace-step_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```

**Request Headers**
| Header | Value |
| --- | --- |
| Content-Type | application/json |
| --- | --- |
| Cache-Control | no-cache |
| Ocp-Apim-Subscription-Key | YOUR_SUBSCRIPTION_KEY |

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
Error Responses

Queue system errors and model validation errors.

Queue System Errors
// 402 — Insufficient balance
```json
{
"error": "Insufficient Balance",
"message": "Your wallet does not have enough balance."
}
```
// 400 — Model not found
```json
{
"error": "Model not found",
"message": "Model 'ace-step' not found or is disabled"
}
```
Error via Status/Webhook
```json
{
"request_id": "ace-step_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "ERROR",
"model_id": "ace-step",
"error": "Description of the error",
"output": null
}
```
Retrieving Results

Poll the universal status endpoint to check progress and retrieve results.

Endpoint
```http
GET https://gateway.pixazo.ai/v2/requests/status/{request_id}
Ocp-Apim-Subscription-Key: YOUR_API_KEY
cURL Example
curl -H "Ocp-Apim-Subscription-Key: YOUR_API_KEY" \
"https://gateway.pixazo.ai/v2/requests/status/ace-step_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
Response (Completed)
{
"request_id": "ace-step_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "COMPLETED",
"model_id": "ace-step",
"error": null,
"output": {
"media_url": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/v1/ace-step_019dxxxx-xxxx/output.ext"
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
| --- | --- | --- |
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

Ace Step 1.5 Music Generation API Pricing
| Resolution | Price (USD) |
| All | $0.01 |
API 
---
### Chatterbox API - AI Text to Speech APIs
**Page:** https://www.pixazo.ai/models/chatterbox


by Resemble-ai

Chatterbox API, developers can convert text into lifelike audio with customizable voice characteristics. The API supports multiple languages and speaking styles, making it ideal for voiceovers, audiobooks, virtual assistants, and accessibility applications requiring human-quality speech output.

Chatterbox v1
Text To Speech


Text To Speech

**Request Code**

**Request Parameters**

**Example Request**

**Response**

**Request Headers**

**Response Handling**

#### Chatterbox v1 Text To Speech API Documentation
`https://gateway.pixazo.ai/chatterbox-text-to-speech/v1`

**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| --- | --- | --- | --- |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |

#### Chatterbox Text to Speech generate request - Chatterbox Text to Speech API

**Request Code**
```http
POST https://gateway.pixazo.ai/chatterbox-text-to-speech/v1/chatterbox-text-to-speech-request
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY

{
"text": "Hello world, this is a test of the Chatterbox text to speech model.",
"audio_url": "https://storage.googleapis.com/chatterbox-demo-samples/prompts/male_rickmorty.mp3",
"exaggeration": 0.25,
"temperature": 0.7,
"cfg": 0.5
}
```

**Output**
```json
{
"request_id": "chatterbox-text-to-speech_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/chatterbox-text-to-speech_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
Webhook (Optional)

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Chatterbox Text to Speech generate request
| Parameter | Required | Type | Description |
| --- | --- | --- | --- |
| text | Yes | string | The textual content to convert into speech. Must be a valid string of readable language. |
| audio_url | No | string | A URL pointing to an audio file (e.g., MP3) to serve as a voice reference. Used to clone or adapt the speaking style. |
| exaggeration | No | number | Controls the degree of expressive emphasis in the generated speech. Higher values increase modulation (e.g., intonation, stress). Range: 0.0 to 1.0. |
| temperature | No | number | Controls randomness in voice generation. Higher values increase variability in pitch and timing; lower values produce more consistent, predictable speech. Range: 0.1 to 1.0. |
| cfg | No | number | Classifier-Free Guidance strength. Influences how closely the output adheres to the input prompt and reference audio. Higher values increase fidelity. Range: 0.0 to 2.0. |

**Example Request**
```json
{
"text": "Hello world, this is a test of the Chatterbox text to speech model.",
"audio_url": "https://storage.googleapis.com/chatterbox-demo-samples/prompts/male_rickmorty.mp3",
"exaggeration": 0.25,
"temperature": 0.7,
"cfg": 0.5
}
```

**Response**
```json
{
"request_id": "chatterbox-text-to-speech_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/chatterbox-text-to-speech_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```

**Request Headers**
| Header | Value |
| --- | --- |
| Content-Type | application/json |
| --- | --- |
| Cache-Control | no-cache |
| Ocp-Apim-Subscription-Key | YOUR_SUBSCRIPTION_KEY |

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
Error Responses

Queue system errors and model validation errors.

Queue System Errors
// 402 — Insufficient balance
```json
{
"error": "Insufficient Balance",
"message": "Your wallet does not have enough balance. Required: $0.01"
}
```
// 400 — Model not found
```json
{
"error": "Model not found",
"message": "Model 'chatterbox-text-to-speech' not found or is disabled"
}
```
Error via Status/Webhook
```json
{
"request_id": "chatterbox-text-to-speech_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "ERROR",
"model_id": "chatterbox-text-to-speech",
"error": "Description of the error",
"output": null
}
```
Retrieving Results

Poll the universal status endpoint to check progress and retrieve results.

Endpoint
```http
GET https://gateway.pixazo.ai/v2/requests/status/{request_id}
Ocp-Apim-Subscription-Key: YOUR_API_KEY
cURL Example
curl -H "Ocp-Apim-Subscription-Key: YOUR_API_KEY" \
"https://gateway.pixazo.ai/v2/requests/status/chatterbox-text-to-speech_019d42ce-bc92-7f98-8181-b42db433b9f2e"
Response (Completed)
{
"request_id": "chatterbox-text-to-speech_019d42ce-bc92-7f98-8181-b42db433b9f2e",
"status": "COMPLETED",
"model_id": "chatterbox-text-to-speech",
"error": null,
"output": {
"media_url": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/v1/chatterbox-text-to-speech_019d42ce-bc92-7f98-8181-b42db433b9f2e/output.wav"
],
"media_type": "audio/wav"
},
"created_at": "2026-03-31T07:32:03.749Z",
"updated_at": "2026-03-31T07:32:20.000Z",
"completed_at": "2026-03-31T07:32:20.000Z"
}
```
Response Fields
| Field | Type | Description |
| --- | --- | --- |
| request_id | string | Unique request identifier |
| status | string | QUEUED, PROCESSING, COMPLETED, FAILED, or ERROR |
| model_id | string | Model that processed the request |
| error | string|null | Error message if failed |
| output.media_url | array | URLs to generated media (R2 CDN) |
| output.media_type | string | MIME type (audio/wav) |
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

Chatterbox v1 Text To Speech API Pricing
| Resolution | Price (USD) |
| All Resolution | $0.03 |
API 
---
### Minimax Music 2.5 API, Minimax Image, Minimax 2.6, 1.0 API - AI Music and Image Generation APIs
**Page:** https://www.pixazo.ai/models/minimax


by MiniMax

Minimax Music 2.5 API, developers can access all MiniMax modalities including text-to-video, image generation, voice synthesis, and music creation. The API provides a unified interface for multimodal content generation, ideal for applications requiring diverse media outputs.

MiniMax Speech 2.6 HD
MiniMax Voice Design v1
MiniMax Image 01
Music Generation
Get Audio Result


Music Generation

**Request Code**

**Request Parameters**

**Example Request**

**Response**

**Request Headers**

**Response Handling**
Get Audio Result

#### MiniMax Speech 2.6 HD Music Generation API Documentation
`https://gateway.pixazo.ai/minimax-hailuo-ai-music/v1`

**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| --- | --- | --- | --- |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |

#### Generate Speech Task - MiniMax Hailuo Speech API

**Request Code**
```http
POST https://gateway.pixazo.ai/minimax-hailuo-ai-music/v1/getAudio
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY

{
"text": "Hello, this is a simple text to speech conversion."
}
```

**Output**
```json
{
"request_id": "minimax-hailuo-audio_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/minimax-hailuo-audio_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
Webhook (Optional)

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Generate Speech Task
| Parameter | Required | Type | Description |
| --- | --- | --- | --- |
| text | Yes | string | The text to be synthesized into speech. The length limit is less than 50,000 characters. Paragraph switches are replaced by newlines. To add pauses in speech, use <#x#> between words, where x is the number of seconds, supporting values from 0.01 to 99.99 (up to two decimal places). The text must be syntactically correct for voice pronunciation. |
| model | No | string | Specifies the speech synthesis model. Options include "speech-2.6-hd", "speech-2.6-turbo", "speech-02-hd", "speech-02-turbo", "speech-01-hd", "speech-01-turbo". This affects the voice characteristics of the output. |
| voice_id | No | string | The ID of the target voice. Supports system voices, cloned voices, and AI-generated voices. See voice options below. |
| speed | No | number | Defines the speed of speech. Acceptable range is from 0.5 to 2.0, where a higher value results in faster speech. |
| vol | No | number | Sets the volume of the synthesized speech. The range is (0,10], with higher values yielding louder audio. |
| pitch | No | integer | Adjusts the pitch of the generated speech. The range is [-12, 12], where 0 retains the original tone. |
| emotion | No | string | Controls the emotional tone of the generated speech. Options include "happy", "sad", "angry", "fearful", "disgusted", "surprised", "calm", "fluent", "whisper". |
| audio_sample_rate | No | integer | Audio sample rate. Options: 8000, 16000, 22050, 24000, 32000, 44100. |
| bitrate | No | integer | Audio bitrate. Options: 32000, 64000, 128000, 256000. |
| format | No | string | Audio format. Options: "mp3", "pcm", "flac". |
| channel | No | integer | Audio channels (1=mono, 2=stereo). |
| pronunciation_dict | No | object | Pronunciation rules for specific characters/symbols. Example: {"tone": ["omg/oh my god"]} |
| language_boost | No | string | Language enhancement for minority languages. Options: "Chinese", "Chinese,Yue", "English", "Arabic", "Russian", "Spanish", "French", "Portuguese", "German", "Turkish", "Dutch", "Ukrainian", "Vietnamese", "Indonesian", "Japanese", "Italian", "Korean", "Thai", "Polish", "Romanian", "Greek", "Czech", "Finnish", "Hindi", "Bulgarian", "Danish", "Hebrew", "Malay", "Persian", "Slovak", "Swedish", "Croatian", "Filipino", "Hungarian", "Norwegian", "Slovenian", "Catalan", "Nynorsk", "Tamil", "Afrikaans", "auto" |
| voice_modify | No | object | Voice effect settings. Properties: pitch (-100 to 100), intensity (-100 to 100), timbre (-100 to 100), sound_effects ("spacious_echo", "auditorium_echo", "lofi_telephone", "robotic") |

**Example Request**
```json
{
"text": "Hello, this is an advanced text to speech conversion with custom settings.",
"voice_id": "female-chengshu",
"speed": 1.0,
"emotion": "happy"
}
```

**Response**
```json
{
"request_id": "minimax-hailuo-audio_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/minimax-hailuo-audio_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```

**Request Headers**
| Header | Value |
| --- | --- |
| Content-Type | application/json |
| --- | --- |
| Cache-Control | no-cache |
| Ocp-Apim-Subscription-Key | YOUR_SUBSCRIPTION_KEY |

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
Error Responses

Queue system errors and model validation errors.

Queue System Errors
// 402 — Insufficient balance
```json
{
"error": "Insufficient Balance",
"message": "Your wallet does not have enough balance."
}
```
// 400 — Model not found
```json
{
"error": "Model not found",
"message": "Model 'minimax-hailuo-audio' not found or is disabled"
}
```
Error via Status/Webhook
```json
{
"request_id": "minimax-hailuo-audio_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "ERROR",
"model_id": "minimax-hailuo-audio",
"error": "Description of the error",
"output": null
}
```
Retrieving Results

Poll the universal status endpoint to check progress and retrieve results.

Endpoint
```http
GET https://gateway.pixazo.ai/v2/requests/status/{request_id}
Ocp-Apim-Subscription-Key: YOUR_API_KEY
cURL Example
curl -H "Ocp-Apim-Subscription-Key: YOUR_API_KEY" \
"https://gateway.pixazo.ai/v2/requests/status/minimax-hailuo-audio_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
Response (Completed)
{
"request_id": "minimax-hailuo-audio_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "COMPLETED",
"model_id": "minimax-hailuo-audio",
"error": null,
"output": {
"media_url": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/v1/minimax-hailuo-audio_019dxxxx-xxxx/output.ext"
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
| --- | --- | --- |
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

MiniMax Speech 2.6 HD Music Generation API Pricing
| Resolution | Price (USD) |
| Per 1000 Characters | $0.1 |

#### MiniMax Speech 2.6 HD Get Audio Result API Documentation
`https://gateway.pixazo.ai/minimax-hailuo-ai-music/v1`

**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| --- | --- | --- | --- |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |

#### Get Speech Result - MiniMax Hailuo Speech API

**Request Code**
```http
POST https://gateway.pixazo.ai/minimax-hailuo-ai-music/v1/getAudioResult
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY

{
"task_id": "344614765236532"
}
```

**Output**
```json
{
"request_id": "minimax-hailuo-audio_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/minimax-hailuo-audio_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
Webhook (Optional)

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Get Speech Result
| Parameter | Required | Type | Description |
| --- | --- | --- | --- |
| task_id | Yes | string | Task ID returned from the create audio task. |

**Example Request**
```json
{
"task_id": "344614765236532"
}
```

**Response**
```json
{
"request_id": "minimax-hailuo-audio_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/minimax-hailuo-audio_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```

**Request Headers**
| Header | Value |
| --- | --- |
| Content-Type | application/json |
| --- | --- |
| Cache-Control | no-cache |
| Ocp-Apim-Subscription-Key | YOUR_SUBSCRIPTION_KEY |

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
Error Responses

Queue system errors and model validation errors.

Queue System Errors
// 402 — Insufficient balance
```json
{
"error": "Insufficient Balance",
"message": "Your wallet does not have enough balance."
}
```
// 400 — Model not found
```json
{
"error": "Model not found",
"message": "Model 'minimax-hailuo-audio' not found or is disabled"
}
```
Error via Status/Webhook
```json
{
"request_id": "minimax-hailuo-audio_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "ERROR",
"model_id": "minimax-hailuo-audio",
"error": "Description of the error",
"output": null
}
```
Retrieving Results

Poll the universal status endpoint to check progress and retrieve results.

Endpoint
```http
GET https://gateway.pixazo.ai/v2/requests/status/{request_id}
Ocp-Apim-Subscription-Key: YOUR_API_KEY
cURL Example
curl -H "Ocp-Apim-Subscription-Key: YOUR_API_KEY" \
"https://gateway.pixazo.ai/v2/requests/status/minimax-hailuo-audio_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
Response (Completed)
{
"request_id": "minimax-hailuo-audio_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "COMPLETED",
"model_id": "minimax-hailuo-audio",
"error": null,
"output": {
"media_url": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/v1/minimax-hailuo-audio_019dxxxx-xxxx/output.ext"
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
| --- | --- | --- |
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

MiniMax Speech 2.6 HD Get Audio Result API Pricing
| Resolution | Price (USD) |
| Per 1000 Characters | $0.1 |
2. MiniMax Voice Design v1

#### MiniMax Voice Design v1 Voice Design API Documentation
`https://gateway.pixazo.ai/minimax-voice-design-api-363/v1`

**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| --- | --- | --- | --- |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
MiniMax Voice Design API generate request - MiniMax Voice Design

**Request Code**
POST /minimax-voice-design-api-request HTTP/1.1
Host: gateway.pixazo.ai
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY

```json
{
"prompt": "Bubbly and excitable female pop star interviewee, youthful, slightly breathless, and very enthusiastic",
"preview_text": "Oh my gosh, hi. It iss like so amazing to be here. This new endpoint just dropped on pixazo and the results have been like totally incredible. Use it now, It is gonna be like epic!"
}
```

**Output**
```json
{
"request_id": "minimax-voice-design-api-363_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/minimax-voice-design-api-363_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
Webhook (Optional)

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - MiniMax Voice Design API generate request
| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| prompt | string | Yes | — | A natural language description of the desired voice personality, tone, and characteristics (e.g., age, gender, emotion, style). This defines the unique vocal identity to be synthesized. |
| preview_text | string | Yes | — | The sample text that will be spoken by the generated voice. Must be a natural, expressive phrase that demonstrates the intended vocal style. |
Minimum Request
```json
{
"prompt": "Bubbly and excitable female pop star interviewee, youthful, slightly breathless, and very enthusiastic",
"preview_text": "Oh my gosh, hi. It's like so amazing to be here."
}
```
Full Request (all options)
```json
{
"prompt": "Bubbly and excitable female pop star interviewee, youthful, slightly breathless, and very enthusiastic",
"preview_text": "Oh my gosh, hi. It iss like so amazing to be here. This new endpoint just dropped on pixazo and the results have been like totally incredible. Use it now, It is gonna be like epic!"
}
```

**Response**
```json
{
"request_id": "minimax-voice-design-api-363_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/minimax-voice-design-api-363_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```

**Request Headers**
| Header | Value |
| --- | --- |
| Content-Type | application/json |
| --- | --- |
| Cache-Control | no-cache |
| Ocp-Apim-Subscription-Key | Your API subscription key |

**Response Handling**

Common status codes for MiniMax Voice Design API generate request.

| Code | Meaning |
| 202 | Accepted — Request queued |
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
Error Responses

Queue system errors and model validation errors.

Queue System Errors
// 402 — Insufficient balance
```json
{
"error": "Insufficient Balance",
"message": "Your wallet does not have enough balance."
}
```
// 400 — Model not found
```json
{
"error": "Model not found",
"message": "Model 'minimax-voice-design-api-363' not found or is disabled"
}
```
Error via Status/Webhook
```json
{
"request_id": "minimax-voice-design-api-363_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "ERROR",
"model_id": "minimax-voice-design-api-363",
"error": "Description of the error",
"output": null
}
```
Retrieving Results

Poll the universal status endpoint to check progress and retrieve results.

Endpoint
```http
GET https://gateway.pixazo.ai/v2/requests/status/{request_id}
Ocp-Apim-Subscription-Key: YOUR_API_KEY
cURL Example
curl -H "Ocp-Apim-Subscription-Key: YOUR_API_KEY" \
"https://gateway.pixazo.ai/v2/requests/status/minimax-voice-design-api-363_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
Response (Completed)
{
"request_id": "minimax-voice-design-api-363_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "COMPLETED",
"model_id": "minimax-voice-design-api-363",
"error": null,
"output": {
"media_url": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/v1/minimax-voice-design-api-363_019dxxxx-xxxx/output.ext"
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
| --- | --- | --- |
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

MiniMax Voice Design v1 Voice Design API Pricing
| Resolution | Price (USD) |
| Per 1000 Characters | $0.03 |
3. MiniMax Image 01

#### MiniMax Image 01 Image To Image API Documentation

**Request Code**
```http
POST https://gateway.pixazo.ai/image-generation/v1/i2i
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY

{
"prompt": "A girl looking into the distance from a library window",
"subject_reference": [
{
"type": "character",
"image_file": "https://example.com/input-image.jpg"
}
```
]
}

**Output**
```json
{
"id": "03ff3cd0820949eb8a410056b5f21d38",
"image_urls": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/..."
],
"image_count": 4
}
```
Request Parameters - Image to Image
| Parameter | Required | Type | Description |
| --- | --- | --- | --- |
| prompt | Yes | string | Text description of desired modifications or style to apply to the input image. |
| subject_reference | Yes | array | Array of reference images. Each object contains: `type` ("character", "object", etc.) and `image_file` (URL or base64). Images must be <10MB, JPG/JPEG/PNG format. |
| model | No | string | Model name. Currently only "image-01" is supported. |
| aspect_ratio | No | string | Image aspect ratio. Options: "1:1" (1024x1024), "16:9" (1280x720), "4:3" (1152x864), "3:2" (1248x832), "2:3" (832x1248), "3:4" (864x1152), "9:16" (720x1280), "21:9" (1344x576). |
| width | No | integer | Image width in pixels (512-2048, divisible by 8). Must be used with height. aspect_ratio takes priority if both are provided. |
| height | No | integer | Image height in pixels (512-2048, divisible by 8). Must be used with width. aspect_ratio takes priority if both are provided. |
| response_format | No | string | Response format. Options: "url" (expires in 24 hours) or "base64". |
| seed | No | integer | Random seed for reproducible results. Same seed + parameters = same image. |
| n | No | integer | Number of images to generate (1-9). |
| prompt_optimizer | No | boolean | Enable automatic prompt optimization. |

**Example Request**
```json
{
"prompt": "A futuristic cyberpunk cityscape at night, neon lights, flying cars, dramatic shadows, highly detailed, cinematic lighting",
"subject_reference": [
{
"type": "character",
"image_file": "https://example.com/input-image.jpg"
},
{
"type": "object",
"image_file": "https://example.com/reference-object.png"
}
```
],
"model": "image-01",
"aspect_ratio": "16:9",
"response_format": "url",
"seed": 98765,
"n": 4,
"prompt_optimizer": true
}

**Response**
```json
{
"id": "03ff3cd0820949eb8a410056b5f21d38",
"image_urls": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/minimax_images/i2i-1234567890-123456-1.png",
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/minimax_images/i2i-1234567890-123456-2.png",
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/minimax_images/i2i-1234567890-123456-3.png",
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/minimax_images/i2i-1234567890-123456-4.png"
],
"image_count": 4,
"metadata": {
"success_count": "4",
"failed_count": "0"
},
"base_resp": {
"status_code": 0,
"status_msg": "success"
}
```
}

**Request Headers**
| Header | Value |
| --- | --- |
| Content-Type | application/json |
| --- | --- |
| Cache-Control | no-cache |
| Ocp-Apim-Subscription-Key | YOUR_SUBSCRIPTION_KEY |

**Response Handling**

Common status codes for Image to Image.

| Code | Meaning |
| 200 | Success |
| 400 | Bad Request |
| 401 | Unauthorized |
| 403 | Forbidden |
| 404 | Not Found |
| 429 | Too Many Requests |
| 500 | Internal Server Error |
MiniMax Image 01 Image To Image API Pricing
| Resolution | Price (USD) |
| All Resolution | $0.02 |

#### MiniMax Image 01 Text To Image API Documentation

**Request Code**
```http
POST https://gateway.pixazo.ai/image-generation/v1/t2i
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY

{
"prompt": "A beautiful sunset over mountains"
}
```

**Output**
```json
{
"id": "03ff3cd0820949eb8a410056b5f21d38",
"image_urls": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/minimax_images/t2i-1234567890-123456-1.png"
],
"image_count": 1
}
```
Request Parameters - Text to Image
| Parameter | Required | Type | Description |
| --- | --- | --- | --- |
| prompt | Yes | string | Text description of the image, maximum 1500 characters. |
| model | No | string | Model name. Currently only "image-01" is supported. |
| aspect_ratio | No | string | Image aspect ratio. Options: "1:1" (1024x1024), "16:9" (1280x720), "4:3" (1152x864), "3:2" (1248x832), "2:3" (832x1248), "3:4" (864x1152), "9:16" (720x1280), "21:9" (1344x576). |
| width | No | integer | Image width in pixels (512-2048, divisible by 8). Must be used with height. aspect_ratio takes priority if both are provided. |
| height | No | integer | Image height in pixels (512-2048, divisible by 8). Must be used with width. aspect_ratio takes priority if both are provided. |
| response_format | No | string | Response format. Options: "url" (expires in 24 hours) or "base64". |
| seed | No | integer | Random seed for reproducible results. Same seed + parameters = same image. |
| n | No | integer | Number of images to generate (1-9). |
| prompt_optimizer | No | boolean | Enable automatic prompt optimization. |

**Example Request**
```json
{
"prompt": "A man in a white t-shirt, full-body, standing front view, outdoors, with the Venice Beach sign in the background, Los Angeles. Fashion photography in 90s documentary style, film grain, photorealistic.",
"model": "image-01",
"aspect_ratio": "16:9",
"response_format": "url",
"seed": 12345,
"n": 3,
"prompt_optimizer": true
}
```

**Response**
```json
{
"id": "03ff3cd0820949eb8a410056b5f21d38",
"image_urls": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/minimax_images/t2i-1234567890-123456-1.png",
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/minimax_images/t2i-1234567890-123456-2.png",
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/minimax_images/t2i-1234567890-123456-3.png"
],
"image_count": 3,
"metadata": {
"success_count": "3",
"failed_count": "0"
},
"base_resp": {
"status_code": 0,
"status_msg": "success"
}
```
}

**Request Headers**
| Header | Value |
| --- | --- |
| Content-Type | application/json |
| --- | --- |
| Cache-Control | no-cache |
| Ocp-Apim-Subscription-Key | YOUR_SUBSCRIPTION_KEY |

**Response Handling**

Common status codes for Text to Image.

| Code | Meaning |
| 200 | Success |
| 400 | Bad Request |
| 401 | Unauthorized |
| 403 | Forbidden |
| 404 | Not Found |
| 429 | Too Many Requests |
| 500 | Internal Server Error |
MiniMax Image 01 Text To Image API Pricing
| Resolution | Price (USD) |
| All Resolution | $0.02 |
API 
---
### Tracks API - AI Music Generation APIs
**Page:** https://www.pixazo.ai/models/tracks


by Pixazo

Tracks API, content creators, filmmakers, and musicians can generate high-quality original music tracks for their projects. The API offers intuitive controls for style, tempo, and mood, making professional music creation accessible to users of all skill levels.

View in Playground
Track v1.0
Music Generation


Music Generation

**Request Code**

**Request Parameters**

**Example Request**

**Response**

**Request Headers**

**Response Handling**

#### Track v1.0 Music Generation API Documentation
`https://gateway.pixazo.ai/tracks/v1`

**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| --- | --- | --- | --- |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Generate Music - Tracks

**Request Code**
```http
POST https://gateway.pixazo.ai/tracks/v1/generate
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY

{
"prompt": "A cinematic Hans Zimmer style orchestral piece, building tension with heavy percussion and brass, epic atmosphere",
"lyrics": "",
"instrumental": true,
"duration": 120,
"bpm": 140,
"infer_steps": 25,
"guidance_scale": 7.5,
"seed": 42
}
```

**Output**
```json
{
"request_id": "tracks_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/tracks_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
Webhook (Optional)

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Generate Music
| Parameter | Required | Type | Description |
| --- | --- | --- | --- |
| prompt | Yes | string | Describes the overall musical style, genre, mood, instrumentation, and atmosphere. |
| lyrics | No | string | Temporal script of the song. Controls structure, sections, vocal style, instrumental breaks, and lyrical content. |
| instrumental | No | boolean | If true, generates instrumental-only music (no vocals). |
| duration | No | integer | Target duration in seconds. |
| bpm | No | integer | Target tempo in beats per minute. Beats per minute (30-300). enables auto-detection via LM. |
| infer_steps | No | integer | Number of denoising steps. Base model: 1-200 (recommended 32-64). Higher = better quality but slower. |
| guidance_scale | No | float | Controls how strongly the model follows the prompt. Typical range: 5.0-9.0. |
| seed | No | integer | Used for reproducibility. Same seed and parameters produce similar outputs. Random seed for reproducibility. Use -1 for random seed, or any positive integer for fixed seed. |

**Example Request**
```json
{
"prompt": "A cinematic Hans Zimmer style orchestral piece, building tension with heavy percussion and brass, epic atmosphere",
"lyrics": "",
"instrumental": true,
"duration": 120,
"bpm": 140,
"infer_steps": 25,
"guidance_scale": 7.5,
"seed": 42
}
```

**Response**
```json
{
"request_id": "tracks_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/tracks_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```

**Request Headers**
| Header | Value |
| --- | --- |
| Content-Type | application/json |
| --- | --- |
| Cache-Control | no-cache |
| Ocp-Apim-Subscription-Key | YOUR_SUBSCRIPTION_KEY |

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
Error Responses

Queue system errors and model validation errors.

Queue System Errors
// 402 — Insufficient balance
```json
{
"error": "Insufficient Balance",
"message": "Your wallet does not have enough balance."
}
```
// 400 — Model not found
```json
{
"error": "Model not found",
"message": "Model 'tracks' not found or is disabled"
}
```
Error via Status/Webhook
```json
{
"request_id": "tracks_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "ERROR",
"model_id": "tracks",
"error": "Description of the error",
"output": null
}
```
Retrieving Results

Poll the universal status endpoint to check progress and retrieve results.

Endpoint
```http
GET https://gateway.pixazo.ai/v2/requests/status/{request_id}
Ocp-Apim-Subscription-Key: YOUR_API_KEY
cURL Example
curl -H "Ocp-Apim-Subscription-Key: YOUR_API_KEY" \
"https://gateway.pixazo.ai/v2/requests/status/tracks_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
Response (Completed)
{
"request_id": "tracks_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "COMPLETED",
"model_id": "tracks",
"error": null,
"output": {
"media_url": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/v1/tracks_019dxxxx-xxxx/output.ext"
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
| --- | --- | --- |
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

Track v1.0 Music Generation API Pricing
| Resolution | Price (USD) |
| All | $0 |
API 
---
### XTTS API - AI Voice Cloning & Text to Speech APIs
**Page:** https://www.pixazo.ai/models/xtts


by Xtts

XTTS API, developers can clone voices and generate speech in multiple languages while maintaining the cloned voice characteristics. The API is ideal for content localization, personalized voice experiences, and applications requiring custom voice generation across language barriers.

v2
Text To Speech


Text To Speech

**Request Code**

**Request Parameters**

**Example Request**

**Response**

**Request Headers**

**Response Handling**

#### v2 Text To Speech API Documentation
`https://gateway.pixazo.ai/voice-clone/v1`

**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| --- | --- | --- | --- |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |

#### Text to Speech Request - XTTS V2 API

**Request Code**
```http
POST https://gateway.pixazo.ai/voice-clone/v1/xtts-v2/generate
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY

{
"speaker": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/male.wav",
"text": "Hello! Welcome to our voice cloning service.",
"language": "en"
}
```

**Output**
```json
{
"request_id": "xtts-v2-api_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/xtts-v2-api_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
Webhook (Optional)

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Text to Speech Request
| Parameter | Required | Type | Description |
| --- | --- | --- | --- |
| speaker | Yes | string | URL to speaker audio file (wav, mp3, m4a, ogg, or flv). 3-10 seconds of clear speech recommended |
| text | No | string | Default: "Hi there, I'm your new voice clone. Try your best to upload quality audio", Text to synthesize (max 500 characters recommended) |
| language | No | string | Default: "en", Output language code. Supported: en, es, fr, de, it, pt, pl, tr, ru, nl, cs, ar, zh, hu, ko, hi |
| cleanup_voice | No | boolean | Default: false, Apply denoising to speaker audio. Use for microphone recordings with background noise |
| webhook | No | string | Default: null, Callback URL for completion notification. POST request sent with results when complete |
| webhook_events_filter | No | array | Default: ["*"], Events that trigger webhook. Values: ["*"] (all), ["completed"] (success/failure only) |

**Example Request**
```json
{
"speaker": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/male.wav",
"text": "Hello! Welcome to our voice cloning service.",
"language": "en"
}
```

**Response**
```json
{
"request_id": "xtts-v2-api_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/xtts-v2-api_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```

**Request Headers**
| Header | Value |
| --- | --- |
| Content-Type | application/json |
| --- | --- |
| Cache-Control | no-cache |
| Ocp-Apim-Subscription-Key | YOUR_SUBSCRIPTION_KEY |

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
Error Responses

Queue system errors and model validation errors.

Queue System Errors
// 402 — Insufficient balance
```json
{
"error": "Insufficient Balance",
"message": "Your wallet does not have enough balance."
}
```
// 400 — Model not found
```json
{
"error": "Model not found",
"message": "Model 'xtts-v2-api' not found or is disabled"
}
```
Error via Status/Webhook
```json
{
"request_id": "xtts-v2-api_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "ERROR",
"model_id": "xtts-v2-api",
"error": "Description of the error",
"output": null
}
```
Retrieving Results

Poll the universal status endpoint to check progress and retrieve results.

Endpoint
```http
GET https://gateway.pixazo.ai/v2/requests/status/{request_id}
Ocp-Apim-Subscription-Key: YOUR_API_KEY
cURL Example
curl -H "Ocp-Apim-Subscription-Key: YOUR_API_KEY" \
"https://gateway.pixazo.ai/v2/requests/status/xtts-v2-api_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
Response (Completed)
{
"request_id": "xtts-v2-api_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "COMPLETED",
"model_id": "xtts-v2-api",
"error": null,
"output": {
"media_url": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/v1/xtts-v2-api_019dxxxx-xxxx/output.ext"
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
| --- | --- | --- |
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

v2 Text To Speech API Pricing
| Resolution | Price (USD) |
| All | $0.015 |
API 
---

### Category: 3D Generation

### Hyper3D API - AI 3D Model Generation APIs
**Page:** https://www.pixazo.ai/models/hyper3d


by Hyper3D

Hyper3D API, developers can create production-ready 3D assets for games, simulations, and visualization without manual modeling. The API supports various 3D formats and provides mesh optimization for different platform requirements.

Hyper3D Rodin v1
3D Generation


3D Generation

**Request Code**

**Request Parameters**

**Example Request**

**Response**

**Request Headers**

**Response Handling**

#### Hyper3D Rodin v1 3D Generation API Documentation
`https://gateway.pixazo.ai/hyper3d-rodin-259/v1`

**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| --- | --- | --- | --- |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Hyper3D Rodin generate request - Hyper3D Rodin

**Request Code**
```http
POST https://gateway.pixazo.ai/hyper3d-rodin-259/v1/hyper3d-rodin-request
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY

{
"prompt": "A futuristic robot with sleek metallic design and glowing blue accents",
"input_image_urls": "https://storage.googleapis.com/falserverless/model_tests/video_models/robot.png",
"condition_mode": "concat",
"geometry_file_format": "glb",
"material": "Shaded",
"quality": "medium",
"tier": "Regular"
}
```

**Output**
```json
{
"request_id": "hyper3d-rodin-259_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/hyper3d-rodin-259_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
Webhook (Optional)

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Hyper3D Rodin generate request
| Parameter | Required | Type | Description |
| --- | --- | --- | --- |
| prompt | Yes | string | A detailed textual description of the desired 3D object or scene. Be specific about shape, texture, color, and context. |
| input_image_urls | No | string | URL to a single input image used as a reference for 3D generation. Supported formats: PNG, JPG, JPEG. |
| condition_mode | No | string | Determines how the input image is used in generation. "concat" combines image features with text prompt. Other modes may be supported in future. |
| geometry_file_format | No | string | Output format for the generated 3D geometry. Supported values: "glb", "gltf", "obj". |
| material | No | string | Style of material rendering. Options: "Shaded", "Wireframe", "Flat". |
| quality | No | string | Level of detail and rendering quality. Values: "low", "medium", "high", "ultra". |
| tier | No | string | Processing priority tier. "Regular" is standard; "Premium" may be available for paid plans. |

**Example Request**
```json
{
"prompt": "A futuristic robot with sleek metallic design and glowing blue accents",
"input_image_urls": "https://storage.googleapis.com/falserverless/model_tests/video_models/robot.png",
"condition_mode": "concat",
"geometry_file_format": "glb",
"material": "Shaded",
"quality": "medium",
"tier": "Regular"
}
```

**Response**
```json
{
"request_id": "hyper3d-rodin-259_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/hyper3d-rodin-259_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```

**Request Headers**
| Header | Value |
| --- | --- |
| Content-Type | application/json |
| --- | --- |
| Cache-Control | no-cache |
| Ocp-Apim-Subscription-Key | YOUR_SUBSCRIPTION_KEY |

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
Error Responses

Queue system errors and model validation errors.

Queue System Errors
// 402 — Insufficient balance
```json
{
"error": "Insufficient Balance",
"message": "Your wallet does not have enough balance."
}
```
// 400 — Model not found
```json
{
"error": "Model not found",
"message": "Model 'hyper3d-rodin-259' not found or is disabled"
}
```
Error via Status/Webhook
```json
{
"request_id": "hyper3d-rodin-259_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "ERROR",
"model_id": "hyper3d-rodin-259",
"error": "Description of the error",
"output": null
}
```
Retrieving Results

Poll the universal status endpoint to check progress and retrieve results.

Endpoint
```http
GET https://gateway.pixazo.ai/v2/requests/status/{request_id}
Ocp-Apim-Subscription-Key: YOUR_API_KEY
cURL Example
curl -H "Ocp-Apim-Subscription-Key: YOUR_API_KEY" \
"https://gateway.pixazo.ai/v2/requests/status/hyper3d-rodin-259_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
Response (Completed)
{
"request_id": "hyper3d-rodin-259_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "COMPLETED",
"model_id": "hyper3d-rodin-259",
"error": null,
"output": {
"media_url": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/v1/hyper3d-rodin-259_019dxxxx-xxxx/output.ext"
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
| --- | --- | --- |
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

Hyper3D Rodin v1 3D Generation API Pricing
| Resolution | Price (USD) |
| Per Generation | $0.4 |
API 
---
### Trellis 2 API, Trellis 3D API - AI 3D Model Generation APIs
**Page:** https://www.pixazo.ai/models/trellis3d


by Trellis

Trellis 2 API, developers can transform product photos, concept art, and designs into production-ready 3D assets. The API streamlines 3D content creation for e-commerce, gaming, and AR/VR applications where converting existing 2D assets to 3D provides significant workflow advantages.

Trellis v2
Image To 3D


Image To 3D

**Request Code**

**Request Parameters**

**Example Request**

**Response**

**Request Headers**

**Response Handling**

#### Trellis v2 Image To 3D API Documentation
`https://gateway.pixazo.ai/trellis-2-image-to-3d/v1`

**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| --- | --- | --- | --- |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |

#### Trellis 2 Image to 3D generate request - Trellis 2 Image to 3D API

**Request Code**
```http
POST https://gateway.pixazo.ai/trellis-2-image-to-3d/v1/trellis-2-image-to-3d-request
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY

{
"image_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/f1.png",
"resolution": 1024,
"ss_guidance_strength": 7.5,
"ss_guidance_rescale": 0.7,
"ss_sampling_steps": 12,
"ss_rescale_t": 5,
"shape_slat_guidance_strength": 7.5,
"shape_slat_guidance_rescale": 0.5,
"shape_slat_sampling_steps": 12,
"shape_slat_rescale_t": 3,
"tex_slat_guidance_strength": 1,
"tex_slat_sampling_steps": 12,
"tex_slat_rescale_t": 3,
"decimation_target": 500000,
"texture_size": 2048,
"remesh": true,
"remesh_band": 1
}
```

**Output**
```json
{
"request_id": "trellis-2-image-to-3d_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/trellis-2-image-to-3d_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
Webhook (Optional)

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Trellis 2 Image to 3D generate request
| Parameter | Required | Type | Description |
| --- | --- | --- | --- |
| image_url | Yes | string | URL of the input 2D image to convert into a 3D model. Must be publicly accessible. |
| resolution | No | integer | Resolution of the initial 3D generation pass. Higher values yield finer detail but longer processing. |
| ss_guidance_strength | No | number | Strength of shape guidance during the initial shape generation stage. Higher values enforce structure fidelity. |
| ss_guidance_rescale | No | number | Rescaling factor applied to shape guidance to prevent overfitting. Helps balance creativity and structure. |
| ss_sampling_steps | No | integer | Number of denoising steps during shape generation. More steps improve quality but increase latency. |
| ss_rescale_t | No | integer | Time rescaling parameter for shape generation. Modulates how guidance changes over time steps. |
| shape_slat_guidance_strength | No | number | Strength of shape SLAT (Spatial Latent Attention) guidance during refinement. |
| shape_slat_guidance_rescale | No | number | Rescaling factor for shape SLAT guidance to smooth output. |
| shape_slat_sampling_steps | No | integer | Number of denoising steps in the shape SLAT refinement stage. |
| shape_slat_rescale_t | No | integer | Time rescaling value for shape SLAT refinement. |
| tex_slat_guidance_strength | No | number | Strength of texture SLAT guidance during texturing pass. Controls detail in surface appearance. |
| tex_slat_sampling_steps | No | integer | Number of denoising steps during texture generation. |
| tex_slat_rescale_t | No | integer | Time rescaling value for texture SLAT generation. |
| decimation_target | No | integer | Target number of polygons in the final mesh. Reduces mesh complexity for performance. |
| texture_size | No | integer | Resolution of the generated texture map (e.g., 1024, 2048). Higher values improve texture quality. |
| remesh | No | boolean | Whether to apply topology-preserving remeshing to improve mesh quality. |
| remesh_band | No | integer | Bandwidth parameter for remeshing. Controls edge preservation during topology optimization. |

**Example Request**
```json
{
"image_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/f1.png",
"resolution": 1024,
"ss_guidance_strength": 7.5,
"ss_guidance_rescale": 0.7,
"ss_sampling_steps": 12,
"ss_rescale_t": 5,
"shape_slat_guidance_strength": 7.5,
"shape_slat_guidance_rescale": 0.5,
"shape_slat_sampling_steps": 12,
"shape_slat_rescale_t": 3,
"tex_slat_guidance_strength": 1,
"tex_slat_sampling_steps": 12,
"tex_slat_rescale_t": 3,
"decimation_target": 500000,
"texture_size": 2048,
"remesh": true,
"remesh_band": 1
}
```

**Response**
```json
{
"request_id": "trellis-2-image-to-3d_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/trellis-2-image-to-3d_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```

**Request Headers**
| Header | Value |
| --- | --- |
| Content-Type | application/json |
| --- | --- |
| Cache-Control | no-cache |
| Ocp-Apim-Subscription-Key | YOUR_SUBSCRIPTION_KEY |

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
Error Responses

Queue system errors and model validation errors.

Queue System Errors
// 402 — Insufficient balance
```json
{
"error": "Insufficient Balance",
"message": "Your wallet does not have enough balance."
}
```
// 400 — Model not found
```json
{
"error": "Model not found",
"message": "Model 'trellis-2-image-to-3d' not found or is disabled"
}
```
Error via Status/Webhook
```json
{
"request_id": "trellis-2-image-to-3d_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "ERROR",
"model_id": "trellis-2-image-to-3d",
"error": "Description of the error",
"output": null
}
```
Retrieving Results

Poll the universal status endpoint to check progress and retrieve results.

Endpoint
```http
GET https://gateway.pixazo.ai/v2/requests/status/{request_id}
Ocp-Apim-Subscription-Key: YOUR_API_KEY
cURL Example
curl -H "Ocp-Apim-Subscription-Key: YOUR_API_KEY" \
"https://gateway.pixazo.ai/v2/requests/status/trellis-2-image-to-3d_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
Response (Completed)
{
"request_id": "trellis-2-image-to-3d_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "COMPLETED",
"model_id": "trellis-2-image-to-3d",
"error": null,
"output": {
"media_url": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/v1/trellis-2-image-to-3d_019dxxxx-xxxx/output.ext"
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
| --- | --- | --- |
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

Trellis v2 Image To 3D API Pricing
| Resolution | Price (USD) |
| All Resolution | $0.35 |
API 
---
### Tripo3D API - AI 3D Model Generation APIs
**Page:** https://www.pixazo.ai/models/tripo3d


by Tripo

Tripo3D API, developers can rapidly create 3D assets for prototyping, visualization, and production use. The API supports version 2.5 with improved geometry and texturing, making it suitable for game development, product visualization, and digital twin creation where speed and quality are both essential.

Tripo3D v2.5
Generate 3D Model


Generate 3D Model

**Request Code**

**Request Parameters**

**Example Request**

**Response**

**Request Headers**

**Response Handling**

#### Tripo3D v2.5 Generate 3D Model API Documentation
`https://gateway.pixazo.ai/tripo3d-v2-5-413/v1`

**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| --- | --- | --- | --- |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
Tripo3D v2.5 generate request - Tripo3D v2.5

**Request Code**
```http
POST https://gateway.pixazo.ai/tripo3d-v2-5-413/v1/tripo3d-v2-5-request
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY

{
"texture": "standard",
"texture_alignment": "original_image",
"orientation": "default",
"image_url": "https://platform.tripo3d.ai/assets/front-235queJB.jpg"
}
```

**Output**
```json
{
"request_id": "tripo3d-v2-5-413_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/tripo3d-v2-5-413_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
Webhook (Optional)

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Tripo3D v2.5 generate request
| Parameter | Required | Type | Description |
| --- | --- | --- | --- |
| texture | No | string | Controls the texture quality and style of the generated 3D model. |
| texture_alignment | No | string | Determines how the input image's texture is mapped onto the 3D model surface. |
| orientation | No | string | Specifies the default orientation of the output 3D model. |
| image_url | Yes | string | Publicly accessible URL of the 2D input image to be converted into a 3D model. Must be reachable by the server. |

**Example Request**
```json
{
"texture": "standard",
"texture_alignment": "original_image",
"orientation": "default",
"image_url": "https://platform.tripo3d.ai/assets/front-235queJB.jpg"
}
```

**Response**
```json
{
"request_id": "tripo3d-v2-5-413_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/tripo3d-v2-5-413_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```

**Request Headers**
| Header | Value |
| --- | --- |
| Content-Type | application/json |
| --- | --- |
| Cache-Control | no-cache |
| Ocp-Apim-Subscription-Key | YOUR_SUBSCRIPTION_KEY |

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
Error Responses

Queue system errors and model validation errors.

Queue System Errors
// 402 — Insufficient balance
```json
{
"error": "Insufficient Balance",
"message": "Your wallet does not have enough balance."
}
```
// 400 — Model not found
```json
{
"error": "Model not found",
"message": "Model 'tripo3d-v2-5-413' not found or is disabled"
}
```
Error via Status/Webhook
```json
{
"request_id": "tripo3d-v2-5-413_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "ERROR",
"model_id": "tripo3d-v2-5-413",
"error": "Description of the error",
"output": null
}
```
Retrieving Results

Poll the universal status endpoint to check progress and retrieve results.

Endpoint
```http
GET https://gateway.pixazo.ai/v2/requests/status/{request_id}
Ocp-Apim-Subscription-Key: YOUR_API_KEY
cURL Example
curl -H "Ocp-Apim-Subscription-Key: YOUR_API_KEY" \
"https://gateway.pixazo.ai/v2/requests/status/tripo3d-v2-5-413_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
Response (Completed)
{
"request_id": "tripo3d-v2-5-413_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "COMPLETED",
"model_id": "tripo3d-v2-5-413",
"error": null,
"output": {
"media_url": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/v1/tripo3d-v2-5-413_019dxxxx-xxxx/output.ext"
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
| --- | --- | --- |
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

Tripo3D v2.5 Generate 3D Model API Pricing

No data available

API 
---

### Category: Image Processing

### Bria 2.0 RMBG API - AI Background Remover APIs
**Page:** https://www.pixazo.ai/models/bria


by Bria

Bria 2.0 RMBG API, businesses can access Bria's commercially-licensed models for creating and modifying images at scale. The API is designed for production workflows requiring high-quality outputs with clear intellectual property rights and enterprise-grade reliability.

Bria RMBG 2.0
Background Removal


Background Removal

**Request Code**

**Request Parameters**

**Example Request**

**Response**

**Request Headers**

**Response Handling**

#### Bria RMBG 2.0 Background Removal API Documentation
`https://gateway.pixazo.ai/bria-rmbg-2-0-682/v1`

**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| --- | --- | --- | --- |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |

#### BRIA RMBG 2.0 generate request - BRIA RMBG 2.0 API

**Request Code**
```http
POST https://gateway.pixazo.ai/bria-rmbg-2-0-682/v1/bria-rmbg-2-0-request
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY

{
"image_url": "https://storage.googleapis.com/generativeai-downloads/images/cat.jpg"
}
```

**Output**
```json
{
"request_id": "bria-rmbg-2-0-682_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/bria-rmbg-2-0-682_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
Webhook (Optional)

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - BRIA RMBG 2.0 generate request
| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| image_url | string | Yes | — | URL of the input image from which the background will be removed. Must be publicly accessible. |
Minimum Request
```json
{
"image_url": "https://storage.googleapis.com/generativeai-downloads/images/cat.jpg"
}
```
Full Request (all options)
```json
{
"image_url": "https://storage.googleapis.com/generativeai-downloads/images/cat.jpg"
}
```

**Response**
```json
{
"request_id": "bria-rmbg-2-0-682_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/bria-rmbg-2-0-682_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```

**Request Headers**
| Header | Value |
| --- | --- |
| Content-Type | application/json |
| --- | --- |
| Cache-Control | no-cache |
| Ocp-Apim-Subscription-Key | Your API subscription key |

**Response Handling**

Common status codes for BRIA RMBG 2.0 generate request.

| Code | Meaning |
| 202 | Accepted — Request queued |
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
Error Responses

Queue system errors and model validation errors.

Queue System Errors
// 402 — Insufficient balance
```json
{
"error": "Insufficient Balance",
"message": "Your wallet does not have enough balance."
}
```
// 400 — Model not found
```json
{
"error": "Model not found",
"message": "Model 'bria-rmbg-2-0-682' not found or is disabled"
}
```
Error via Status/Webhook
```json
{
"request_id": "bria-rmbg-2-0-682_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "ERROR",
"model_id": "bria-rmbg-2-0-682",
"error": "Description of the error",
"output": null
}
```
Retrieving Results

Poll the universal status endpoint to check progress and retrieve results.

Endpoint
```http
GET https://gateway.pixazo.ai/v2/requests/status/{request_id}
Ocp-Apim-Subscription-Key: YOUR_API_KEY
cURL Example
curl -H "Ocp-Apim-Subscription-Key: YOUR_API_KEY" \
"https://gateway.pixazo.ai/v2/requests/status/bria-rmbg-2-0-682_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
Response (Completed)
{
"request_id": "bria-rmbg-2-0-682_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "COMPLETED",
"model_id": "bria-rmbg-2-0-682",
"error": null,
"output": {
"media_url": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/v1/bria-rmbg-2-0-682_019dxxxx-xxxx/output.ext"
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
| --- | --- | --- |
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

Bria RMBG 2.0 Background Removal API Pricing
| Resolution | Price (USD) |
| All Resolution | $0.018 |
API 
---
### Crystal Upscaler API - AI Image Upscaling APIs
**Page:** https://www.pixazo.ai/models/crystal-upscaler


by Clarityai

Crystal Upscaler API, developers can upscale low-resolution images up to 4x their original size without the blurriness of traditional methods. The API is perfect for enhancing product photos, restoring old images, preparing content for print, and improving visual assets for high-resolution displays.

Crystal Upscaler v1
Upscale Image


Upscale Image

**Request Code**

**Request Parameters**

**Example Request**

**Response**

**Request Headers**

**Response Handling**

#### Crystal Upscaler v1 Upscale Image API Documentation
`https://gateway.pixazo.ai/upscaler/v1`

**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| --- | --- | --- | --- |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |

#### Image Request - Crystal Upscaler API

**Request Code**
```http
POST https://gateway.pixazo.ai/upscaler/v1/crystal-upscaler/generate
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY

{
"image": "https://example.com/portrait.jpg",
"scale_factor": 4
}
```

**Output**
```json
{
"request_id": "crystal-upscaler_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/crystal-upscaler_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
Webhook (Optional)

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Image Request
| Parameter | Required | Type | Description |
| --- | --- | --- | --- |
| image | Yes | string | Input image URL to upscale. Must be publicly accessible (HTTPS recommended). |
| scale_factor | No | integer | Upscaling factor. Valid values: 2, 4, 6 or 8. |
| webhook | No | string | Callback URL for completion notification. POST request sent with results when complete. |
| webhook_events_filter | No | array | Events that trigger webhook. Valid values: ["*"] (all), ["completed"] (success/failure only). |

**Example Request**
```json
{
"image": "https://example.com/portrait.jpg",
"scale_factor": 4
}
```

**Response**
```json
{
"request_id": "crystal-upscaler_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/crystal-upscaler_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```

**Request Headers**
| Header | Value |
| --- | --- |
| Content-Type | application/json |
| --- | --- |
| Cache-Control | no-cache |
| Ocp-Apim-Subscription-Key | YOUR_SUBSCRIPTION_KEY |

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
Error Responses

Queue system errors and model validation errors.

Queue System Errors
// 402 — Insufficient balance
```json
{
"error": "Insufficient Balance",
"message": "Your wallet does not have enough balance."
}
```
// 400 — Model not found
```json
{
"error": "Model not found",
"message": "Model 'crystal-upscaler' not found or is disabled"
}
```
Error via Status/Webhook
```json
{
"request_id": "crystal-upscaler_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "ERROR",
"model_id": "crystal-upscaler",
"error": "Description of the error",
"output": null
}
```
Retrieving Results

Poll the universal status endpoint to check progress and retrieve results.

Endpoint
```http
GET https://gateway.pixazo.ai/v2/requests/status/{request_id}
Ocp-Apim-Subscription-Key: YOUR_API_KEY
cURL Example
curl -H "Ocp-Apim-Subscription-Key: YOUR_API_KEY" \
"https://gateway.pixazo.ai/v2/requests/status/crystal-upscaler_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
Response (Completed)
{
"request_id": "crystal-upscaler_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "COMPLETED",
"model_id": "crystal-upscaler",
"error": null,
"output": {
"media_url": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/v1/crystal-upscaler_019dxxxx-xxxx/output.ext"
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
| --- | --- | --- |
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

Crystal Upscaler v1 Upscale Image API Pricing
| Resolution | Price (USD) |
| All resolution | $0.07 |
| All resolution | $0.12 |
| Per generation | $0.23 |
| Per generation | $0.45 |
API 
---
### Seed VR API - AI Image & Video Upscaling APIs
**Page:** https://www.pixazo.ai/models/seedvr


by Seed VR

Seed VR API, developers can upscale visual content to higher resolutions suitable for large displays, print, and professional production. The API handles both static images and video frames, making it a comprehensive solution for quality enhancement workflows.

SeedVR2
Upscale Image
Upscale Video


Upscale Image

**Request Code**

**Request Parameters**

**Example Request**

**Response**

**Request Headers**

**Response Handling**
Upscale Video

#### SeedVR2 Upscale Image API Documentation
`https://gateway.pixazo.ai/seedvr-upscale/v1`

**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| --- | --- | --- | --- |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |

#### Upscale Image Request - SeedVR Upscale API

**Request Code**
```http
POST https://gateway.pixazo.ai/seedvr-upscale/v1/upscale-image/generate
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY

{
"image_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/vt_human.jpg",
"upscale_factor": 2,
"output_format": "png"
}
```

**Output**
```json
{
"request_id": "seedvr-upscale_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/seedvr-upscale_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
Webhook (Optional)

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Upscale Image Request
| Parameter | Required | Type | Description |
| --- | --- | --- | --- |
| image_url | Yes | string | URL of the image to upscale. Must be publicly accessible (HTTPS recommended). Supported formats: PNG, JPEG, WebP. |
| upscale_mode | No | string | Upscaling mode. Valid values: `factor` (multiply resolution), `target` (specific resolution). |
| upscale_factor | No | number | Upscale multiplier (1-8). Only used with `upscale_mode: "factor"`. Higher values = larger output. Recommended: 2-4 for best results. |
| target_resolution | No | string | Target output resolution. Valid values: `720p`, `1080p`, `1440p`, `2160p`. Only used with `upscale_mode: "target"`. |
| noise_scale | No | number | Noise reduction strength (0.0-1.0). Lower = preserve details/texture, Higher = smoother/cleaner. Recommended: 0.05-0.15 for photos, 0.08-0.12 for digital art. |
| output_format | No | string | Output image format. Valid values: `jpg` (smaller files, photos), `png` (lossless, graphics), `webp` (modern, efficient). |
| seed | No | integer | Random seed for reproducibility. Use the same seed to get consistent results across runs. |
| sync_mode | No | boolean | Synchronous mode. If true, returns image as data URI (not recommended for production). |
| webhook | No | string | Callback URL for completion notification. POST request sent with upscaling results when complete. |
| webhook_events_filter | No | array | Events that trigger webhook. Valid values: `["*"]` (all events), `["completed"]` (success/failure only). |

**Example Request**
```json
{
"image_url": "https://pub-582b7213209642b9b995c96c95a30381.r2.dev/vt_human.jpg",
"upscale_factor": 2,
"output_format": "png"
}
```

**Response**
```json
{
"request_id": "seedvr-upscale_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/seedvr-upscale_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```

**Request Headers**
| Header | Value |
| --- | --- |
| Content-Type | application/json |
| --- | --- |
| Cache-Control | no-cache |
| Ocp-Apim-Subscription-Key | YOUR_SUBSCRIPTION_KEY |

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
Error Responses

Queue system errors and model validation errors.

Queue System Errors
// 402 — Insufficient balance
```json
{
"error": "Insufficient Balance",
"message": "Your wallet does not have enough balance."
}
```
// 400 — Model not found
```json
{
"error": "Model not found",
"message": "Model 'seedvr-upscale' not found or is disabled"
}
```
Error via Status/Webhook
```json
{
"request_id": "seedvr-upscale_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "ERROR",
"model_id": "seedvr-upscale",
"error": "Description of the error",
"output": null
}
```
Retrieving Results

Poll the universal status endpoint to check progress and retrieve results.

Endpoint
```http
GET https://gateway.pixazo.ai/v2/requests/status/{request_id}
Ocp-Apim-Subscription-Key: YOUR_API_KEY
cURL Example
curl -H "Ocp-Apim-Subscription-Key: YOUR_API_KEY" \
"https://gateway.pixazo.ai/v2/requests/status/seedvr-upscale_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
Response (Completed)
{
"request_id": "seedvr-upscale_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "COMPLETED",
"model_id": "seedvr-upscale",
"error": null,
"output": {
"media_url": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/v1/seedvr-upscale_019dxxxx-xxxx/output.ext"
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
| --- | --- | --- |
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

SeedVR2 Upscale Image API Pricing
| Resolution | Price (USD) |
| All Resolution | $0.001 |

#### SeedVR2 Upscale Video API Documentation
`https://gateway.pixazo.ai/seedvr-upscale/v1`

**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| --- | --- | --- | --- |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |

#### Upscale Video Request - SeedVR Upscale API

**Request Code**
```http
POST https://gateway.pixazo.ai/seedvr-upscale/v1/upscale-video/generate
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY

{
"video_url": "https://example.com/my-video.mp4",
"upscale_factor": 2
}
```

**Output**
```json
{
"request_id": "seedvr-upscale_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/seedvr-upscale_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
Webhook (Optional)

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - Upscale Video Request
| Parameter | Required | Type | Description |
| --- | --- | --- | --- |
| video_url | Yes | string | URL of the video to upscale. Must be publicly accessible (HTTPS recommended). Supported formats: MP4, MOV, AVI, WebM. |
| upscale_mode | No | string | Upscaling mode. Valid values: `factor` (multiply resolution), `target` (specific resolution). |
| upscale_factor | No | number | Upscale multiplier (1-4). Only used with `upscale_mode: "factor"`. Higher values = larger output. Use 1 for quality enhancement without size increase. |
| target_resolution | No | string | Target output resolution. Valid values: `720p`, `1080p`. Only used with `upscale_mode: "target". |
| noise_scale | No | number | Noise reduction strength (0.0-1.0). Lower = preserve details/grain, Higher = smoother/cleaner. Recommended: 0.05-0.2. |
| output_format | No | string | Output video codec. Valid values: `X264 (.mp4)` (widely compatible), `H265 (.mp4)` (better compression, newer devices). |
| output_quality | No | string | Output encoding quality. Valid values: `low` (faster, smaller file), `medium` (balanced), `high` (best quality, recommended). |
| output_write_mode | No | string | Encoding speed/quality tradeoff. Valid values: `fast` (quick encoding), `balanced` (recommended), `quality` (slower, best quality). |
| seed | No | integer | Random seed for reproducibility. Use the same seed to get consistent results across runs. |
| webhook | No | string | Callback URL for completion notification. POST request sent with upscaling results when complete. |
| webhook_events_filter | No | array | Events that trigger webhook. Valid values: `["*"]` (all events), `["completed"]` (success/failure only), `["start", "output", "completed"]`. |

**Example Request**
```json
{
"video_url": "https://example.com/my-video.mp4",
"upscale_factor": 2
}
```

**Response**
```json
{
"request_id": "seedvr-upscale_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/seedvr-upscale_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```

**Request Headers**
| Header | Value |
| --- | --- |
| Content-Type | application/json |
| --- | --- |
| Cache-Control | no-cache |
| Ocp-Apim-Subscription-Key | YOUR_SUBSCRIPTION_KEY |
Checking Status

After submitting your request, use this endpoint to check status and retrieve results.

Endpoint

```http
POST https://gateway.pixazo.ai/seedvr-upscale/v1/upscale-video/prediction

Request Body
{
"prediction_id": "abc123xyz789..."
}
```
Code Examples
```http
POST https://gateway.pixazo.ai/seedvr-upscale/v1/upscale-video/prediction
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY

{
"prediction_id": "abc123xyz789..."
}
```
Response Example
```json
{
"success": true,
"id": "abc123xyz789...",
"status": "processing",
"input": {
"video_url": "https://example.com/video.mp4",
"upscale_mode": "factor",
"upscale_factor": 2,
"noise_scale": 0.1,
"output_format": "X264 (.mp4)",
"output_quality": "high",
"output_write_mode": "balanced"
},
"created_at": "2025-10-27T06:38:11.001Z"
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
Error Responses

Queue system errors and model validation errors.

Queue System Errors
// 402 — Insufficient balance
```json
{
"error": "Insufficient Balance",
"message": "Your wallet does not have enough balance."
}
```
// 400 — Model not found
```json
{
"error": "Model not found",
"message": "Model 'seedvr-upscale' not found or is disabled"
}
```
Error via Status/Webhook
```json
{
"request_id": "seedvr-upscale_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "ERROR",
"model_id": "seedvr-upscale",
"error": "Description of the error",
"output": null
}
```
Retrieving Results

Poll the universal status endpoint to check progress and retrieve results.

Endpoint
```http
GET https://gateway.pixazo.ai/v2/requests/status/{request_id}
Ocp-Apim-Subscription-Key: YOUR_API_KEY
cURL Example
curl -H "Ocp-Apim-Subscription-Key: YOUR_API_KEY" \
"https://gateway.pixazo.ai/v2/requests/status/seedvr-upscale_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
Response (Completed)
{
"request_id": "seedvr-upscale_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "COMPLETED",
"model_id": "seedvr-upscale",
"error": null,
"output": {
"media_url": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/v1/seedvr-upscale_019dxxxx-xxxx/output.ext"
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
| --- | --- | --- |
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

SeedVR2 Upscale Video API Pricing
| Resolution | Price (USD) |
| 1920 × 1080 | $0.68 |
| 1280 × 720 | $0.12 |
| 3840 × 2160 | $0.82 |
| 2560 × 1440 | $1.95 |
| Per generation | $0 |
API 
---

### Category: Virtual Try-On

### IDM VTON API - AI Virtual Try-On APIs
**Page:** https://www.pixazo.ai/models/idm-vton


by IDM-VTON

IDM VTON API, fashion retailers and e-commerce platforms can implement realistic virtual fitting rooms. The API handles various clothing types and body poses, creating convincing visualizations that help customers make confident purchasing decisions.

IDM VTON v1
Virtual Try-On


Virtual Try-On

**Request Code**

**Request Parameters**

**Example Request**

**Response**

**Request Headers**

**Response Handling**

#### IDM VTON v1 Virtual Try-On API Documentation

**Request Code**
```http
POST https://gateway.pixazo.ai/idm-vton-api/v1/r-idm-vton
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: your-subscription-key

{
"garm_img": "https://example.com/garment.jpg",
"human_img": "https://example.com/human.jpg",
"garment_des": "A blue cotton dress",
"category": "dress"
}
```

**Output**
```json
{
"result_url": "https://result.pixazo.ai/output.jpg",
"status": "completed",
"job_set_id": "job-12345-abcde",
"processing_time": 2.4
}
```
Request Parameters - idm-vton
| Parameter | Required | Type | Description |
| --- | --- | --- | --- |
| garm_img | Yes | string | URL of the garment image to be tried on |
| human_img | Yes | string | URL of the human model image |
| garment_des | Yes | string | Description of the garment for better generation |
| category | Yes | string | Category of garment (e.g., dress, shirt, pants) |

**Example Request**
```json
{
"garm_img": "https://example.com/garment.jpg",
"human_img": "https://example.com/human.jpg",
"garment_des": "A blue cotton dress",
"category": "dress"
}
```

**Response**
```json
{
"result_url": "https://result.pixazo.ai/output.jpg",
"status": "completed",
"job_set_id": "job-12345-abcde",
"processing_time": 2.4
}
```

**Request Headers**
| Header | Value |
| --- | --- |
| Content-Type | application/json |
| --- | --- |
| Cache-Control | no-cache |
| Ocp-Apim-Subscription-Key | your-subscription-key |

**Response Handling**

Common status codes for idm-vton.

| Code | Meaning |
| 200 | Success |
| 400 | Bad Request |
| 401 | Unauthorized |
| 403 | Forbidden |
| 404 | Not Found |
| 429 | Too Many Requests |
| 500 | Internal Server Error |
IDM VTON v1 Virtual Try-On API Pricing
| Resolution | Price (USD) |
| All Resolution | $0.05 |
API 
---

### Category: Lipsync & Avatar

### OmniHuman 1.5 API - AI Lipsync & Video Generation APIs
**Page:** https://www.pixazo.ai/models/omnihuman


by BytePlus

OmniHuman 1.5 API, developers can synchronize any audio with video to produce natural lip movements, facial expressions, and head motion. The API excels at multilingual dubbing, avatar animation, and creating talking head videos for education, marketing, and entertainment. OmniHuman's sophisticated algorithms ensure natural-looking results that maintain the character and emotion of the original content.

OmniHuman v1.5
Lipsync Generation


Lipsync Generation

**Request Code**

**Request Parameters**

**Example Request**

**Response**

**Request Headers**

**Response Handling**

#### OmniHuman v1.5 Lipsync Generation API Documentation
`https://gateway.pixazo.ai/bytedance-omnihuman-v1-5-290/v1`

**Authentication**

All requests require an API key passed via header.

| Header | Type | Required | Description |
| --- | --- | --- | --- |
| Ocp-Apim-Subscription-Key | string | Yes | Your API subscription key |
ByteDance Omnihuman v1.5 generate request - ByteDance Omnihuman v1.5

**Request Code**
```http
POST https://gateway.pixazo.ai/bytedance-omnihuman-v1-5-290/v1/bytedance-omnihuman-v1-5-request
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_SUBSCRIPTION_KEY

{
"image_url": "https://storage.googleapis.com/falserverless/example_inputs/omnihuman_v15_input_image.png",
"audio_url": "https://storage.googleapis.com/falserverless/example_inputs/omnihuman_v15_input_audio.mp3",
"resolution": "1080p"
}
```

**Output**
```json
{
"request_id": "bytedance-omnihuman-v1-5-290_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/bytedance-omnihuman-v1-5-290_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```
Webhook (Optional)

Add the X-Webhook-URL header to your generate request to receive a POST callback instead of polling.

X-Webhook-URL: https://your-server.com/webhook/callback
Request Parameters - ByteDance Omnihuman v1.5 generate request
| Parameter | Required | Type | Description |
| --- | --- | --- | --- |
| image_url | Yes | string | Publicly accessible URL to a static image of a human face or full-body portrait. The image should clearly show the subject’s face for accurate animation. |
| audio_url | Yes | string | Publicly accessible URL to an audio file (MP3 or WAV) containing the speech to be synchronized with the subject’s lip movements. |
| resolution | No | string | Output video resolution. Supports "720p", "1080p", and "480p". Higher resolutions result in larger file sizes and longer processing times. |

**Example Request**
```json
{
"image_url": "https://storage.googleapis.com/falserverless/example_inputs/omnihuman_v15_input_image.png",
"audio_url": "https://storage.googleapis.com/falserverless/example_inputs/omnihuman_v15_input_audio.mp3",
"resolution": "1080p"
}
```

**Response**
```json
{
"request_id": "bytedance-omnihuman-v1-5-290_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "QUEUED",
"polling_url": "https://gateway.pixazo.ai/v2/requests/status/bytedance-omnihuman-v1-5-290_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```

**Request Headers**
| Header | Value |
| --- | --- |
| Content-Type | application/json |
| --- | --- |
| Cache-Control | no-cache |
| Ocp-Apim-Subscription-Key | YOUR_SUBSCRIPTION_KEY |

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
Error Responses

Queue system errors and model validation errors.

Queue System Errors
// 402 — Insufficient balance
```json
{
"error": "Insufficient Balance",
"message": "Your wallet does not have enough balance."
}
```
// 400 — Model not found
```json
{
"error": "Model not found",
"message": "Model 'bytedance-omnihuman-v1-5-290' not found or is disabled"
}
```
Error via Status/Webhook
```json
{
"request_id": "bytedance-omnihuman-v1-5-290_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "ERROR",
"model_id": "bytedance-omnihuman-v1-5-290",
"error": "Description of the error",
"output": null
}
```
Retrieving Results

Poll the universal status endpoint to check progress and retrieve results.

Endpoint
```http
GET https://gateway.pixazo.ai/v2/requests/status/{request_id}
Ocp-Apim-Subscription-Key: YOUR_API_KEY
cURL Example
curl -H "Ocp-Apim-Subscription-Key: YOUR_API_KEY" \
"https://gateway.pixazo.ai/v2/requests/status/bytedance-omnihuman-v1-5-290_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
Response (Completed)
{
"request_id": "bytedance-omnihuman-v1-5-290_019dxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
"status": "COMPLETED",
"model_id": "bytedance-omnihuman-v1-5-290",
"error": null,
"output": {
"media_url": [
"https://pub-582b7213209642b9b995c96c95a30381.r2.dev/v1/bytedance-omnihuman-v1-5-290_019dxxxx-xxxx/output.ext"
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
| --- | --- | --- |
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

OmniHuman v1.5 Lipsync Generation API Pricing
| Resolution | Price (USD) |
| Per second of output video | $0.16 |
API 
---

### Category: Coming Soon

#### DALL-E — Coming Soon
**Page:** https://www.pixazo.ai/models/dalle
> API documentation not yet available.

---
