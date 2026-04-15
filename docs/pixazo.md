# Pixazo API — Complete Model Reference

> Source: [pixazo.ai/models](https://www.pixazo.ai/models)  
> Gateway: `https://gateway.pixazo.ai`  
> Last updated: 2026-04-15

---

## Authentication

All requests require an API key via header:

```
Ocp-Apim-Subscription-Key: YOUR_API_KEY
```

Get your API key at [pixazo.ai](https://www.pixazo.ai).

---

## Common API Patterns

### Async Polling Flow

Most models use an async queue pattern:

1. **Submit** a POST request → get `request_id` + status `IN_QUEUE`
2. **Poll** the result endpoint every 5-10s
3. **Download** from output URL when `status == "COMPLETED"`

```
IN_QUEUE → IN_PROGRESS → COMPLETED
                       → FAILED
```

### Universal Status Endpoint

```http
GET https://gateway.pixazo.ai/v2/requests/status/{request_id}
Ocp-Apim-Subscription-Key: YOUR_API_KEY
```

**Response (completed):**
```json
{
  "request_id": "a1b2c3d4-e5f6-7890-g1h2-i3j4k5l6m7n8",
  "status": "COMPLETED",
  "output": {
    "media_url": ["https://storage.googleapis.com/output/image_1.png"],
    "media_type": "image/png"
  },
  "created_at": "2026-04-15T10:00:00.000Z",
  "completed_at": "2026-04-15T10:00:15.000Z"
}
```

### Webhook (Optional)

Add `X-Webhook-URL` header to receive a POST callback instead of polling:

```
X-Webhook-URL: https://your-server.com/webhook/callback
```

### Common Status Codes

| Code | Meaning |
|------|---|
| 200  | Success |
| 202  | Accepted — Request queued |
| 400  | Bad Request — Invalid parameters |
| 401  | Unauthorized — Missing or invalid API key |
| 402  | Insufficient Balance |
| 403  | Forbidden |
| 404  | Not Found |
| 429  | Too Many Requests — Rate limit exceeded |
| 500  | Internal Server Error |

### Common Headers

```
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_API_KEY
```

### URL Pattern

All endpoints follow:
```
POST https://gateway.pixazo.ai/{api-id}/v1/{operation}
```

> **Note:** Older documentation may reference `gateway.appypie.com`. Use `gateway.pixazo.ai` for all new integrations.

---

## Table of Contents

1. [Text-to-Image](#text-to-image)
2. [Image-to-Image](#image-to-image)
3. [Image Editing](#image-editing)
4. [Image Restoration & Upscaling](#image-restoration--upscaling)
5. [Text-to-Video](#text-to-video)
6. [Image-to-Video](#image-to-video)
7. [Video Editor](#video-editor)
8. [Speech-to-Video](#speech-to-video)
9. [Reference-to-Image](#reference-to-image)
10. [Reference-to-Video](#reference-to-video)
11. [Consistent Character](#consistent-character)
12. [Virtual Try-On](#virtual-try-on)
13. [Lipsync](#lipsync)
14. [Text-to-Speech](#text-to-speech)
15. [Audio & Music Generation](#audio--music-generation)
16. [Voice Cloning](#voice-cloning)
17. [3D Models](#3d-models)
18. [Background Remover](#background-remover)
19. [Tools & Training](#tools--training)
20. [API ID Quick Reference](#api-id-quick-reference)

---

## Text-to-Image

### Flux Schnell

| Field | Value |
|-------|-------|
| Provider | Black Forest Labs |
| API ID | `flux-1-schnell` |
| Endpoint | `POST https://gateway.pixazo.ai/flux-1-schnell/v1/getData` |
| Type | Synchronous |

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| prompt | string | Yes | Text description of the image to generate |
| num_steps | integer | No | Number of inference steps (default: 4) |
| seed | integer | No | Random seed for reproducibility |
| height | integer | No | Image height in pixels (default: 512) |
| width | integer | No | Image width in pixels (default: 512) |

**Example Request:**

<details>
<summary>HTTP</summary>

```http
POST https://gateway.pixazo.ai/flux-1-schnell/v1/getData HTTP/1.1
Content-Type: application/json
Cache-Control: no-cache
Ocp-Apim-Subscription-Key: YOUR_API_KEY

{
    "prompt": "A female skateboarder executing a trick at the Paris Olympics with the Eiffel Tower in the background.",
    "num_steps": 4,
    "seed": 15,
    "height": 512,
    "width": 512
}
```
</details>

<details>
<summary>Python</summary>

```python
import requests

url = "https://gateway.pixazo.ai/flux-1-schnell/v1/getData"
headers = {
    "Content-Type": "application/json",
    "Cache-Control": "no-cache",
    "Ocp-Apim-Subscription-Key": "YOUR_API_KEY"
}
data = {
    "prompt": "A female skateboarder executing a trick at the Paris Olympics with the Eiffel Tower in the background.",
    "num_steps": 4,
    "seed": 15,
    "height": 512,
    "width": 512
}

response = requests.post(url, json=data, headers=headers)
print(response.json())
```
</details>

<details>
<summary>JavaScript</summary>

```javascript
const body = {
    "prompt": "A female skateboarder executing a trick at the Paris Olympics with the Eiffel Tower in the background.",
    "num_steps": 4,
    "seed": 15,
    "height": 512,
    "width": 512
};

fetch("https://gateway.pixazo.ai/flux-1-schnell/v1/getData", {
    method: "POST",
    body: JSON.stringify(body),
    headers: {
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
        "Ocp-Apim-Subscription-Key": "YOUR_API_KEY"
    }
})
.then(response => response.json())
.then(data => console.log(data))
.catch(err => console.error(err));
```
</details>

<details>
<summary>cURL</summary>

```bash
curl -X POST "https://gateway.pixazo.ai/flux-1-schnell/v1/getData" \
  -H "Content-Type: application/json" \
  -H "Cache-Control: no-cache" \
  -H "Ocp-Apim-Subscription-Key: YOUR_API_KEY" \
  -d '{"prompt": "A female skateboarder executing a trick at the Paris Olympics with the Eiffel Tower in the background.", "num_steps": 4, "seed": 15, "height": 512, "width": 512}'
```
</details>

<details>
<summary>Java</summary>

```java
import java.net.URI;
import java.net.http.*;

public class FluxSchnell {
    public static void main(String[] args) throws Exception {
        String json = "{\"prompt\": \"A female skateboarder executing a trick at the Paris Olympics\", \"num_steps\": 4, \"seed\": 15, \"height\": 512, \"width\": 512}";
        HttpClient client = HttpClient.newHttpClient();
        HttpRequest request = HttpRequest.newBuilder()
            .uri(URI.create("https://gateway.pixazo.ai/flux-1-schnell/v1/getData"))
            .header("Content-Type", "application/json")
            .header("Ocp-Apim-Subscription-Key", "YOUR_API_KEY")
            .POST(HttpRequest.BodyPublishers.ofString(json))
            .build();
        HttpResponse<String> response = client.send(request, HttpResponse.BodyHandlers.ofString());
        System.out.println(response.body());
    }
}
```
</details>

<details>
<summary>PHP</summary>

```php
<?php
$url = "https://gateway.pixazo.ai/flux-1-schnell/v1/getData";
$headers = [
    "Content-Type: application/json",
    "Cache-Control: no-cache",
    "Ocp-Apim-Subscription-Key: YOUR_API_KEY"
];
$data = json_encode([
    "prompt" => "A female skateboarder executing a trick at the Paris Olympics with the Eiffel Tower in the background.",
    "num_steps" => 4,
    "seed" => 15,
    "height" => 512,
    "width" => 512
]);
$ch = curl_init($url);
curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
curl_setopt($ch, CURLOPT_POST, true);
curl_setopt($ch, CURLOPT_POSTFIELDS, $data);
curl_setopt($ch, CURLOPT_HTTPHEADER, $headers);
$resp = curl_exec($ch);
curl_close($ch);
echo $resp;
?>
```
</details>

---

### Flux Dev

| Field | Value |
|-------|-------|
| Provider | Black Forest Labs |
| API ID | `flux-dev` |
| Endpoint | `POST https://gateway.pixazo.ai/flux-dev/v1/getData` |
| Type | Synchronous |
| Description | Cutting-edge text-to-image model excelling at high-quality visuals from textual descriptions with exceptional prompt adherence, visual fidelity, and image diversity. |

---

### Flux Pro

| Field | Value |
|-------|-------|
| Provider | Black Forest Labs |
| API ID | `flux-pro` |
| Endpoint | `POST https://gateway.pixazo.ai/flux-pro/v1/getData` |
| Type | Synchronous |
| Description | State-of-the-art image generation with exceptional prompt following, outstanding visual quality, intricate detail, and impressive output diversity. |

---

### Flux 1.1 Pro Ultra

| Field | Value |
|-------|-------|
| Provider | Black Forest Labs |
| API ID | `flux-1-1-ultra` |
| Endpoint | `POST https://gateway.pixazo.ai/flux-1-1-ultra/v1/getData` |
| Type | Synchronous |
| Description | Hyper-realistic image generation with remarkable detail and precision. Designed for businesses, developers, and creators. |

---

### Flux 2

| Field | Value |
|-------|-------|
| Provider | Black Forest Labs |
| API ID | `flux-2` |
| Endpoint | `POST https://gateway.pixazo.ai/flux-2/v1/generate` |
| Pixazo Page | [/models/text-to-image/flux-2-api](https://www.pixazo.ai/models/text-to-image/flux-2-api) |

---

### Ideogram V2

| Field | Value |
|-------|-------|
| Provider | Ideogram AI |
| API ID | `ideogramV_2` |
| Endpoint | `POST https://gateway.pixazo.ai/ideogramV_2/v1/generate` |
| Type | Synchronous |

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| image_request.prompt | string | Yes | Text description of the image |
| image_request.negative_prompt | string | No | What to avoid in the image |
| image_request.model | string | No | Model version (e.g., "V_2") |
| image_request.aspect_ratio | string | No | e.g., "ASPECT_10_16", "ASPECT_1_1" |
| image_request.magic_prompt_option | string | No | "AUTO", "ON", "OFF" |
| image_request.seed | integer | No | Random seed |
| image_request.style_type | string | No | "AUTO", "REALISTIC", "DESIGN", "3D", "ANIME" |
| image_request.color_palette.name | string | No | e.g., "JUNGLE", "PASTEL", "NEON" |

<details>
<summary>Python</summary>

```python
import requests

url = "https://gateway.pixazo.ai/ideogramV_2/v1/generate"
headers = {
    "Content-Type": "application/json",
    "Ocp-Apim-Subscription-Key": "YOUR_API_KEY"
}
data = {
    "image_request": {
        "prompt": "A serene tropical beach scene with tall palm trees against a sunset sky.",
        "negative_prompt": "blur",
        "model": "V_2",
        "aspect_ratio": "ASPECT_10_16",
        "magic_prompt_option": "AUTO",
        "seed": 212,
        "style_type": "AUTO",
        "color_palette": {"name": "JUNGLE"}
    }
}

response = requests.post(url, json=data, headers=headers)
print(response.json())
```
</details>

<details>
<summary>JavaScript</summary>

```javascript
const body = {
    image_request: {
        prompt: "A serene tropical beach scene with tall palm trees against a sunset sky.",
        negative_prompt: "blur",
        model: "V_2",
        aspect_ratio: "ASPECT_10_16",
        magic_prompt_option: "AUTO",
        seed: 212,
        style_type: "AUTO",
        color_palette: { name: "JUNGLE" }
    }
};

fetch("https://gateway.pixazo.ai/ideogramV_2/v1/generate", {
    method: "POST",
    headers: {
        "Content-Type": "application/json",
        "Ocp-Apim-Subscription-Key": "YOUR_API_KEY"
    },
    body: JSON.stringify(body)
})
.then(r => r.json()).then(console.log);
```
</details>

<details>
<summary>cURL</summary>

```bash
curl -X POST "https://gateway.pixazo.ai/ideogramV_2/v1/generate" \
  -H "Content-Type: application/json" \
  -H "Ocp-Apim-Subscription-Key: YOUR_API_KEY" \
  -d '{"image_request": {"prompt": "A serene tropical beach scene", "model": "V_2", "aspect_ratio": "ASPECT_10_16", "style_type": "AUTO"}}'
```
</details>

---

### Kling AI Image Generation

| Field | Value |
|-------|-------|
| Provider | Kling AI |
| API ID | `kling-ai-image` |
| Endpoint | `POST https://gateway.pixazo.ai/kling-ai-image/v1/getImageTask` |
| Type | Async |

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| prompt | string | Yes | Text description of the image |

<details>
<summary>Python</summary>

```python
import requests

url = "https://gateway.pixazo.ai/kling-ai-image/v1/getImageTask"
headers = {
    "Content-Type": "application/json",
    "Ocp-Apim-Subscription-Key": "YOUR_API_KEY"
}
data = {"prompt": "Sparrow bird flying"}

response = requests.post(url, json=data, headers=headers)
print(response.json())
```
</details>

<details>
<summary>cURL</summary>

```bash
curl -X POST "https://gateway.pixazo.ai/kling-ai-image/v1/getImageTask" \
  -H "Content-Type: application/json" \
  -H "Ocp-Apim-Subscription-Key: YOUR_API_KEY" \
  -d '{"prompt": "Sparrow bird flying"}'
```
</details>

---

### SDXL (Stable Diffusion XL)

| Field | Value |
|-------|-------|
| Provider | Stability AI |
| API ID | `getImage` |
| Endpoint | `POST https://gateway.pixazo.ai/getImage/v1/getSDXLImage` |
| Type | Synchronous |

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| prompt | string | Yes | Text description |
| negative_prompt | string | No | What to avoid |
| height | integer | No | Image height (default: 1024) |
| width | integer | No | Image width (default: 1024) |
| num_steps | integer | No | Inference steps (default: 20) |
| guidance_scale | float | No | CFG scale (default: 5) |
| seed | integer | No | Random seed |

**Pricing:** Basic $19/mo (5K calls), Standard $49/mo (15K calls), Pro $99/mo (40K calls)

<details>
<summary>Python</summary>

```python
import requests

url = "https://gateway.pixazo.ai/getImage/v1/getSDXLImage"
headers = {
    "Content-Type": "application/json",
    "Ocp-Apim-Subscription-Key": "YOUR_API_KEY"
}
data = {
    "prompt": "High-resolution realistic image of a sparrow bird perched on a cherry blossom branch.",
    "negative_prompt": "Low-quality, blurry",
    "height": 1024,
    "width": 1024,
    "num_steps": 20,
    "guidance_scale": 5,
    "seed": 40
}

response = requests.post(url, json=data, headers=headers)
print(response.json())
```
</details>

<details>
<summary>cURL</summary>

```bash
curl -X POST "https://gateway.pixazo.ai/getImage/v1/getSDXLImage" \
  -H "Content-Type: application/json" \
  -H "Ocp-Apim-Subscription-Key: YOUR_API_KEY" \
  -d '{"prompt": "A sparrow bird on a cherry blossom branch", "negative_prompt": "blurry", "height": 1024, "width": 1024, "num_steps": 20, "guidance_scale": 5, "seed": 40}'
```
</details>

---

### SDXL Lightning

| Field | Value |
|-------|-------|
| Provider | Stability AI |
| API ID | `sdxl_lightning/getImage` |
| Endpoint | `POST https://gateway.pixazo.ai/sdxl_lightning/getImage/v1/getSDXLImage` |
| Type | Synchronous |
| Description | Lightning-fast version of SDXL with reduced inference steps. |

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| prompt | string | Yes | Text description |
| negativePrompt | string | No | What to avoid |
| height | integer | No | Image height (default: 1024) |
| width | integer | No | Image width (default: 1024) |
| num_steps | integer | No | Inference steps (default: 20) |
| guidance | float | No | Guidance scale (default: 5) |
| seed | integer | No | Random seed |

<details>
<summary>Python</summary>

```python
import requests

url = "https://gateway.pixazo.ai/sdxl_lightning/getImage/v1/getSDXLImage"
headers = {
    "Content-Type": "application/json",
    "Ocp-Apim-Subscription-Key": "YOUR_API_KEY"
}
data = {
    "prompt": "A mystical phoenix rising from golden flames, its fiery wings lighting up the night sky.",
    "negativePrompt": "blurry, low quality",
    "height": 1024,
    "width": 1024,
    "num_steps": 20,
    "guidance": 5,
    "seed": 42
}

response = requests.post(url, json=data, headers=headers)
print(response.json())
```
</details>

<details>
<summary>cURL</summary>

```bash
curl -X POST "https://gateway.pixazo.ai/sdxl_lightning/getImage/v1/getSDXLImage" \
  -H "Content-Type: application/json" \
  -H "Ocp-Apim-Subscription-Key: YOUR_API_KEY" \
  -d '{"prompt": "A mystical phoenix rising from golden flames", "negativePrompt": "blurry", "height": 1024, "width": 1024, "num_steps": 20, "guidance": 5, "seed": 42}'
```
</details>

---

### Stable Diffusion

| Field | Value |
|-------|-------|
| Provider | Stability AI |
| API ID | `getImage` |
| Endpoint | `POST https://gateway.pixazo.ai/getImage/v1/getSDXLImage` |
| Type | Synchronous |
| Note | Shares the same endpoint as SDXL |

Same parameters and usage as [SDXL](#sdxl-stable-diffusion-xl).

---

### Stable Diffusion Inpainting

| Field | Value |
|-------|-------|
| Provider | Stability AI |
| API ID | `inpainting` |
| Endpoint | `POST https://gateway.pixazo.ai/inpainting/v1/getImage` |
| Type | Synchronous |

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| prompt | string | Yes | What to generate in the masked area |
| imageUrl | string | Yes | URL of the original image |
| maskUrl | string | Yes | URL of the mask image (white = edit area) |

<details>
<summary>Python</summary>

```python
import requests

url = "https://gateway.pixazo.ai/inpainting/v1/getImage"
headers = {
    "Content-Type": "application/json",
    "Ocp-Apim-Subscription-Key": "YOUR_API_KEY"
}
data = {
    "prompt": "Change to a lion",
    "imageUrl": "https://example.com/photo.png",
    "maskUrl": "https://example.com/mask.png"
}

response = requests.post(url, json=data, headers=headers)
print(response.json())
```
</details>

<details>
<summary>cURL</summary>

```bash
curl -X POST "https://gateway.pixazo.ai/inpainting/v1/getImage" \
  -H "Content-Type: application/json" \
  -H "Ocp-Apim-Subscription-Key: YOUR_API_KEY" \
  -d '{"prompt": "Change to a lion", "imageUrl": "https://example.com/photo.png", "maskUrl": "https://example.com/mask.png"}'
```
</details>

---

### Additional Text-to-Image Models

| Model | Provider | API ID | Endpoint | Pixazo Page |
|-------|----------|--------|----------|-------------|
| GPT Image 1.5 | OpenAI | `gpt-image-1-5` | `/v1/generate` | [Link](https://www.pixazo.ai/models/text-to-image/gpt-image-1-5-api) |
| Hunyuan Image 3.0 | Tencent | `hunyuan-image-3-0` | `/v1/generate` | [Link](https://www.pixazo.ai/models/text-to-image/hunyuan-image-3-0-api) |
| Longcat Image | Longcat AI | `longcat-image` | `/v1/generate` | [Link](https://www.pixazo.ai/models/text-to-image/longcat-image-api) |
| Seedream V4 | ByteDance | `seedream-v4` | `/v1/generate` | [Link](https://www.pixazo.ai/models/text-to-image/seedream-v4-api) |
| Z-Image Turbo | Z-AI | `z-image-turbo` | `/v1/generate` | [Link](https://www.pixazo.ai/models/text-to-image/z-image-turbo-api) |
| Qwen Image | Alibaba | `qwen-image` | `/v1/generate` | [Link](https://www.pixazo.ai/models/text-to-image/qwen-image-api) |
| PixelForge T2I | Pixazo AI | `pixelforge` | `/v1/generate` | [Link](https://www.pixazo.ai/models/text-to-image/pixelforge-api) |
| PixelYatra | Pixazo AI | `pixelyatra` | `/v1/generate` | [Link](https://www.pixazo.ai/models/text-to-image/pixelyatra-api) |
| Higgsfield Soul | Higgsfield | `soul` | `/v1/generate` | [Link](https://www.pixazo.ai/models/text-to-image/soul-api) |
| Wan 2.5 T2I | Alibaba | `wan-2-5` | `/v1/generate` | [Link](https://www.pixazo.ai/models/text-to-image/wan-2.5-api) |
| Ghibli Style | Community | `ghibli-style` | `/v1/generate` | [Link](https://www.pixazo.ai/models/text-to-image/ghibli-style) |

---

## Image-to-Image

### Nano Banana Pro

| Field | Value |
|-------|-------|
| Provider | Google (Gemini 3 Pro) |
| API ID | `nano-banana-pro-770` |
| Generate | `POST https://gateway.pixazo.ai/nano-banana-pro-770/v1/nano-banana-pro-request` |
| Poll Result | `POST https://gateway.pixazo.ai/nano-banana-pro-770/v1/nano-banana-pro-request-result` |
| Type | Async (queue → poll) |
| Pricing | $0.045 per request |

**Parameters (Generate):**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| prompt | string | Yes | Text describing the desired transformation |
| image_urls | array of strings | Yes | HTTPS URLs of input images (up to 14) |

**Generate Response:**
```json
{"status": "IN_QUEUE", "request_id": "a1b2c3d4-e5f6-7890-g1h2-i3j4k5l6m7n8"}
```

**Poll Response (Completed):**
```json
{
  "status": "COMPLETED",
  "request_id": "a1b2c3d4-e5f6-7890-g1h2-i3j4k5l6m7n8",
  "images": [
    {"url": "https://storage.googleapis.com/output/image_1.png", "width": 1024, "height": 1024, "content_type": "image/png"}
  ],
  "metadata": {"processing_time_seconds": 12.4, "input_image_count": 2}
}
```

<details>
<summary>Python</summary>

```python
import requests, time

BASE = "https://gateway.pixazo.ai/nano-banana-pro-770/v1"
HEADERS = {
    "Content-Type": "application/json",
    "Ocp-Apim-Subscription-Key": "YOUR_API_KEY"
}

# Step 1: Submit
resp = requests.post(f"{BASE}/nano-banana-pro-request", json={
    "prompt": "make a photo of the man driving the car down the california coastline",
    "image_urls": [
        "https://storage.googleapis.com/falserverless/example_inputs/nano-banana-edit-input.png",
        "https://storage.googleapis.com/falserverless/example_inputs/nano-banana-edit-input-2.png"
    ]
}, headers=HEADERS)
request_id = resp.json()["request_id"]

# Step 2: Poll
while True:
    result = requests.post(f"{BASE}/nano-banana-pro-request-result",
        json={"request_id": request_id}, headers=HEADERS).json()
    if result["status"] == "COMPLETED":
        print(result["images"][0]["url"])
        break
    elif result["status"] == "FAILED":
        print("Error:", result["error"])
        break
    time.sleep(5)
```
</details>

<details>
<summary>JavaScript</summary>

```javascript
const BASE = "https://gateway.pixazo.ai/nano-banana-pro-770/v1";
const HEADERS = {
    "Content-Type": "application/json",
    "Ocp-Apim-Subscription-Key": "YOUR_API_KEY"
};

// Step 1: Submit
const submitResp = await fetch(`${BASE}/nano-banana-pro-request`, {
    method: "POST",
    headers: HEADERS,
    body: JSON.stringify({
        prompt: "make a photo of the man driving the car down the california coastline",
        image_urls: [
            "https://storage.googleapis.com/falserverless/example_inputs/nano-banana-edit-input.png",
            "https://storage.googleapis.com/falserverless/example_inputs/nano-banana-edit-input-2.png"
        ]
    })
});
const { request_id } = await submitResp.json();

// Step 2: Poll
let result;
do {
    await new Promise(r => setTimeout(r, 5000));
    const pollResp = await fetch(`${BASE}/nano-banana-pro-request-result`, {
        method: "POST", headers: HEADERS,
        body: JSON.stringify({ request_id })
    });
    result = await pollResp.json();
} while (result.status !== "COMPLETED" && result.status !== "FAILED");

console.log(result.images?.[0]?.url || result.error);
```
</details>

<details>
<summary>cURL</summary>

```bash
# Submit
curl -X POST "https://gateway.pixazo.ai/nano-banana-pro-770/v1/nano-banana-pro-request" \
  -H "Content-Type: application/json" \
  -H "Ocp-Apim-Subscription-Key: YOUR_API_KEY" \
  -d '{"prompt": "man driving car down california coastline", "image_urls": ["https://example.com/input.png"]}'

# Poll result
curl -X POST "https://gateway.pixazo.ai/nano-banana-pro-770/v1/nano-banana-pro-request-result" \
  -H "Content-Type: application/json" \
  -H "Ocp-Apim-Subscription-Key: YOUR_API_KEY" \
  -d '{"request_id": "a1b2c3d4-e5f6-7890-g1h2-i3j4k5l6m7n8"}'
```
</details>

---

### Additional Image-to-Image Models

| Model | API ID | Endpoint | Page |
|-------|--------|----------|------|
| Crystal Upscaler | `crystal-upscaler` | `/v1/upscale` | [Link](https://www.pixazo.ai/models/image-to-image/crystal-upscaler-api) |
| Qwen Image Layered | `qwen-image-layered` | `/v1/generate` | [Link](https://www.pixazo.ai/models/image-to-image/qwen-image-layered-api) |
| SeedEdit V3 I2I | `seededit-v3-image-to-image` | `/v1/generate` | [Link](https://www.pixazo.ai/models/image-to-image/seededit-v3-image-to-image-api) |
| Wan 2.5 I2I | `wan-2-5-i2i` | `/v1/generate` | [Link](https://www.pixazo.ai/models/image-to-image/wan-2-5-api) |
| PixelForge I2I | `pixelforge-i2i` | `/v1/generate` | [Link](https://www.pixazo.ai/models/image-to-image/pixelforge-api) |

---

## Image Editing

### Nano Banana Image Edit

| Field | Value |
|-------|-------|
| Provider | Google (Gemini 2.5 Flash) |
| API ID | `nano-banana-image-edit` |
| Endpoint | `POST https://gateway.pixazo.ai/nano-banana-image-edit/v1/edit` |
| Description | All-in-one AI for creating and editing images. Add/remove objects, change colors/lighting, backgrounds, extend edges, blend images. |
| Pixazo Page | [Link](https://www.pixazo.ai/models/image-editing/google-gemini-2.5-flash-nano-banana-image-edit-api) |

### Additional Image Editing Models

| Model | API ID | Page |
|-------|--------|------|
| Qwen Image Edit | `qwen-image-edit` | [Link](https://www.pixazo.ai/models/image-editing/qwen-image-edit-api) |
| Qwen Image Edit Plus LoRA | `qwen-image-edit-plus-lora` | [Link](https://www.pixazo.ai/models/image-editing/qwen-image-edit-plus-lora-api) |
| Reve Edit Image | `reve-edit-image` | [Link](https://www.pixazo.ai/models/image-editing/reve-edit-image-api) |
| Seedream 4.5 | `seedream-4-5` | [Link](https://www.pixazo.ai/models/image-editing/seedream-4-5-api) |
| FireRed Image Edit | `firered-image-edit` | [Link](https://www.pixazo.ai/models/firered-image-edit) |
| Lucy Edit | `lucy-edit` | [Link](https://www.pixazo.ai/models/lucy-edit) |

---

## Image Restoration & Upscaling

### AI Image Upscaler

| Model | API ID | Page |
|-------|--------|------|
| Flux.1-dev ControlNet | `flux-1-dev-controlnet` | [Link](https://www.pixazo.ai/models/ai-image-upscaler/flux.1-dev-controlnet-api) |
| SeedVR2 Image | `seedvr2-image` | [Link](https://www.pixazo.ai/models/ai-image-upscaler/seedvr2-image-api) |
| Topaz | `topaz` | [Link](https://www.pixazo.ai/models/topaz) |

### AI Video Upscaler

| Model | API ID | Page |
|-------|--------|------|
| SeedVR | `seedvr` | [Link](https://www.pixazo.ai/models/ai-video-upscaler/seedvr-api) |

### Image Restoration

| Model | API ID | Description | Page |
|-------|--------|-------------|------|
| BSRGAN | `bsrgan` | Blind super-resolution for low-quality images | [Link](https://www.pixazo.ai/models/image-restoration/bsrgan-api) |
| Flux 1 Kontext | `flux-1-kontext` | Context-aware image restoration | [Link](https://www.pixazo.ai/models/image-restoration/flux-1-kontext-api) |
| CodeFormer | `sczhou-codeformer` | Face restoration model | [Link](https://www.pixazo.ai/models/image-restoration/sczhou-codeformer-api) |

---

## Text-to-Video

### Kling AI T2V

| Field | Value |
|-------|-------|
| Provider | Kling AI |
| API ID | `kling-ai-video` |
| Endpoint | `POST https://gateway.pixazo.ai/kling-ai-video/v1/generateVideoTask` |
| Type | Async |

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| prompt | string | Yes | Text description of the video |
| negative_prompt | string | No | What to avoid |

**Pricing:** Basic $19/mo (200 calls), Standard $49/mo (400 calls, $0.30 overage), Pro $99/mo (750 calls, $0.20 overage). 10s plans: Standard $129-149/mo, Pro $199/mo.

<details>
<summary>Python</summary>

```python
import requests

url = "https://gateway.pixazo.ai/kling-ai-video/v1/generateVideoTask"
headers = {
    "Content-Type": "application/json",
    "Ocp-Apim-Subscription-Key": "YOUR_API_KEY"
}
data = {
    "prompt": "An enchanted forest with glowing mushrooms, fireflies, and a sparkling river flowing through the trees.",
    "negative_prompt": "nude, porn, abusive"
}

response = requests.post(url, json=data, headers=headers)
print(response.json())
```
</details>

<details>
<summary>cURL</summary>

```bash
curl -X POST "https://gateway.pixazo.ai/kling-ai-video/v1/generateVideoTask" \
  -H "Content-Type: application/json" \
  -H "Ocp-Apim-Subscription-Key: YOUR_API_KEY" \
  -d '{"prompt": "An enchanted forest with glowing mushrooms and fireflies", "negative_prompt": "low quality"}'
```
</details>

---

### MiniMax Hailuo AI T2V

| Field | Value |
|-------|-------|
| Provider | MiniMax |
| API ID | `minimax-hailuo-ai` |
| Endpoint | `POST https://gateway.pixazo.ai/minimax-hailuo-ai/v1/generate` |
| Type | Async |

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| prompt | string | Yes | Detailed text description of the video |

<details>
<summary>Python</summary>

```python
import requests

url = "https://gateway.pixazo.ai/minimax-hailuo-ai/v1/generate"
headers = {
    "Content-Type": "application/json",
    "Ocp-Apim-Subscription-Key": "YOUR_API_KEY"
}
data = {
    "prompt": "A bear leaping into a fast-flowing river to catch a fish, with lush green forest and distant mountains."
}

response = requests.post(url, json=data, headers=headers)
print(response.json())
```
</details>

---

### Additional Text-to-Video Models

| Model | API ID | Page |
|-------|--------|------|
| Hailuo 2.3 Pro | `hailuo-2-3-pro` | [Link](https://www.pixazo.ai/models/text-to-video/hailuo-2-3-pro-api) |
| LTX 2 Video | `ltx-2-video` | [Link](https://www.pixazo.ai/models/text-to-video/ltx-2-video-api) |
| Seedance Pro T2V | `seedance-pro` | [Link](https://www.pixazo.ai/models/text-to-video/seedance-pro-api) |
| Sora 2 T2V | `sora-2` | [Link](https://www.pixazo.ai/models/text-to-video/sora-2-api) |
| Veo 3.1 T2V | `veo3-1` | [Link](https://www.pixazo.ai/models/text-to-video/veo3-1-api) |
| Wan 2.1 T2V | `wan2-1` | [Link](https://www.pixazo.ai/models/text-to-video/wan2.1-api) |
| Wan 2.2 T2V | `wan2-2` | [Link](https://www.pixazo.ai/models/text-to-video/wan2.2-api) |
| Wan 2.5 T2V | `wan-2-5` | [Link](https://www.pixazo.ai/models/text-to-video/wan-2.5-api) |

---

## Image-to-Video

### Kling AI I2V

| Field | Value |
|-------|-------|
| Provider | Kling AI |
| API ID | `kling-ai-video` |
| Endpoint | `POST https://gateway.pixazo.ai/kling-ai-video/v1/getImageToVideoTask` |
| Type | Async |

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| image | string | Yes | URL of the source image |
| negative_prompt | string | No | What to avoid |

<details>
<summary>Python</summary>

```python
import requests

url = "https://gateway.pixazo.ai/kling-ai-video/v1/getImageToVideoTask"
headers = {
    "Content-Type": "application/json",
    "Ocp-Apim-Subscription-Key": "YOUR_API_KEY"
}
data = {
    "image": "https://example.com/photo.jpg",
    "negative_prompt": "Fade"
}

response = requests.post(url, json=data, headers=headers)
print(response.json())
```
</details>

<details>
<summary>cURL</summary>

```bash
curl -X POST "https://gateway.pixazo.ai/kling-ai-video/v1/getImageToVideoTask" \
  -H "Content-Type: application/json" \
  -H "Ocp-Apim-Subscription-Key: YOUR_API_KEY" \
  -d '{"image": "https://example.com/photo.jpg", "negative_prompt": "Fade"}'
```
</details>

---

### MiniMax Hailuo AI I2V

| Field | Value |
|-------|-------|
| Provider | MiniMax |
| API ID | `minimax-hailuo-ai` |
| Endpoint | `POST https://gateway.pixazo.ai/minimax-hailuo-ai/v1/generate` |
| Type | Async |

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| first_frame_image | string | Yes | URL of the source image |

<details>
<summary>Python</summary>

```python
import requests

url = "https://gateway.pixazo.ai/minimax-hailuo-ai/v1/generate"
headers = {
    "Content-Type": "application/json",
    "Ocp-Apim-Subscription-Key": "YOUR_API_KEY"
}
data = {
    "first_frame_image": "https://example.com/photo.jpg"
}

response = requests.post(url, json=data, headers=headers)
print(response.json())
```
</details>

---

### Higgsfield DoP (Director of Photography)

| Field | Value |
|-------|-------|
| Provider | Higgsfield |
| API ID | `dop` |
| Endpoint | `POST https://gateway.pixazo.ai/dop/v1/generate` |
| Type | Async |
| Pixazo Page | [Link](https://www.pixazo.ai/models/image-to-video/dop-api) |

---

### Additional Image-to-Video Models

| Model | API ID | Page |
|-------|--------|------|
| Baidu GenFlare 2.0 | `baidu-genflare-2-0` | [Link](https://www.pixazo.ai/models/image-to-video/baidu-genflare-2-0-api) |
| Hailuo 2.3 Fast | `hailuo-2-3-fast` | [Link](https://www.pixazo.ai/models/image-to-video/hailuo-2-3-fast-api) |
| Kandinsky 5.0 Pro | `kandinsky-5-0-pro` | [Link](https://www.pixazo.ai/models/image-to-video/kandinsky-5-0-pro-api) |
| Kling AI Avatar V2 Pro | `kling-ai-avatar-v2-pro` | [Link](https://www.pixazo.ai/models/image-to-video/kling-ai-avatar-v2-pro-api) |
| Kling O1 | `kling-o1` | [Link](https://www.pixazo.ai/models/image-to-video/kling-o1-api) |
| Kling Video 2.6 | `kling-video-2-6` | [Link](https://www.pixazo.ai/models/image-to-video/kling-video-2-6-api) |
| Kling Video 2.6 Motion Control | `kling-video-v2-6-motion-control` | [Link](https://www.pixazo.ai/models/image-to-video/kling-video-v2-6-motion-control-api) |
| LTX 2 19B | `ltx-2-19b` | [Link](https://www.pixazo.ai/models/image-to-video/ltx-2-19b-api) |
| LTX 2 Video I2V | `ltx-2-video` | [Link](https://www.pixazo.ai/models/image-to-video/ltx-2-video-api) |
| Seedance 1.5 | `seedance-1-5` | [Link](https://www.pixazo.ai/models/image-to-video/seedance-1-5-api) |
| Seedance Pro I2V | `seedance-pro` | [Link](https://www.pixazo.ai/models/image-to-video/seedance-pro-api) |
| Sora 2 I2V | `sora-2` | [Link](https://www.pixazo.ai/models/image-to-video/sora-2-api) |
| Veed Fabric 1.0 | `veed-fabric-1-0` | [Link](https://www.pixazo.ai/models/image-to-video/veed-fabric-1-0-api) |
| Veo 3.1 I2V | `veo3-1` | [Link](https://www.pixazo.ai/models/image-to-video/veo3-1-api) |
| Wan 2.1 I2V | `wan2-1` | [Link](https://www.pixazo.ai/models/image-to-video/wan2.1-api) |
| Wan 2.2 I2V | `wan2-2` | [Link](https://www.pixazo.ai/models/image-to-video/wan2.2-api) |
| Wan 2.2 Animate | `wan-2-2-animate` | [Link](https://www.pixazo.ai/models/image-to-video/wan-2-2-animate-api) |
| Wan 2.5 I2V | `wan-2-5` | [Link](https://www.pixazo.ai/models/image-to-video/wan-2.5-api) |
| Wan 2.6 | `wan2-6` | [Link](https://www.pixazo.ai/models/image-to-video/wan2.6-api) |

---

## Video Editor

| Model | API ID | Description | Page |
|-------|--------|-------------|------|
| Lucy Edit Fast | `lucy-edit-fast` | Fast video editing | [Link](https://www.pixazo.ai/models/video-editor/lucy-edit-fast-api) |
| Luma Modify Video | `luma-modify-video` | AI video modification | [Link](https://www.pixazo.ai/models/video-editor/luma-modify-video-api) |
| Luma Reframe Video | `luma-reframe-video` | Video reframing | [Link](https://www.pixazo.ai/models/video-editor/luma-reframe-video-api) |
| Runway Gen4 Aleph | `runwayml-gen4-aleph` | Next-gen video editing | [Link](https://www.pixazo.ai/models/video-editor/runwayml-gen4-aleph-api) |

---

## Speech-to-Video

| Model | API ID | Description | Page |
|-------|--------|-------------|------|
| Wan 2.2 14B | `wan-2-2-14b` | 14B parameter multimodal speech-to-video | [Link](https://www.pixazo.ai/models/speech-to-video/wan-2-2-14b-api) |

---

## Reference-to-Image

| Model | API ID | Description | Page |
|-------|--------|-------------|------|
| Reve Remix | `reve-remix` | Remix images with reference styles | [Link](https://www.pixazo.ai/models/reference-to-image/reve-remix-api) |
| Seedream Edit Multi-Image | `seedream-edit-multi-image` | Multi-reference image editing | [Link](https://www.pixazo.ai/models/reference-to-image/seedream-edit-multi-image) |

---

## Reference-to-Video

| Model | API ID | Description | Page |
|-------|--------|-------------|------|
| Seedance Frame-to-Video | `seedance-frame-to-video` | Reference frames to video | [Link](https://www.pixazo.ai/models/reference-to-video/seedance-frame-to-video-api) |
| Veo 3.1 Ref-to-Video | `veo3-1-ref` | Reference-guided video | [Link](https://www.pixazo.ai/models/reference-to-video/veo3-1-api) |

---

## Consistent Character

| Model | API ID | Description | Page |
|-------|--------|-------------|------|
| Higgsfield Soul ID | `soul-id` | Turn photos into consistent high-fashion AI characters | [Link](https://www.pixazo.ai/models/consistent-character/soul-id-api) |

---

## Virtual Try-On

### IDM-VTON

| Field | Value |
|-------|-------|
| Provider | IDM |
| API ID | `idm-vton-api` |
| Endpoint | `POST https://gateway.pixazo.ai/idm-vton-api/v1/r-idm-vton` |
| Type | Async |

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| garm_img | string | Yes | URL of the garment image |
| human_img | string | Yes | URL of the person image |
| garment_des | string | No | Description of the garment |
| category | string | No | e.g., "upper_body", "lower_body", "dresses" |

<details>
<summary>Python</summary>

```python
import requests

url = "https://gateway.pixazo.ai/idm-vton-api/v1/r-idm-vton"
headers = {
    "Content-Type": "application/json",
    "Ocp-Apim-Subscription-Key": "YOUR_API_KEY"
}
data = {
    "garm_img": "https://example.com/garment.jpg",
    "human_img": "https://example.com/person.jpg",
    "garment_des": "red summer dress",
    "category": "dresses"
}

response = requests.post(url, json=data, headers=headers)
print(response.json())
```
</details>

---

### Kolors Virtual Try-On

| Field | Value |
|-------|-------|
| Provider | Kling AI |
| API ID | `kling-ai-vton` |
| Endpoint | `POST https://gateway.pixazo.ai/kling-ai-vton/v1/getVirtualTryOnTask` |
| Type | Async |

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| human_image | string | Yes | URL of the person image |
| cloth_image | string | Yes | URL of the clothing image |
| callback_url | string | No | Webhook callback URL |

<details>
<summary>Python</summary>

```python
import requests

url = "https://gateway.pixazo.ai/kling-ai-vton/v1/getVirtualTryOnTask"
headers = {
    "Content-Type": "application/json",
    "Ocp-Apim-Subscription-Key": "YOUR_API_KEY"
}
data = {
    "human_image": "https://example.com/person.jpg",
    "cloth_image": "https://example.com/top.jpeg",
    "callback_url": ""
}

response = requests.post(url, json=data, headers=headers)
print(response.json())
```
</details>

---

### Pixelforge Clothing VTON

| Field | Value |
|-------|-------|
| Provider | Pixazo AI |
| API ID | `virtual-tryon` |
| Endpoint | `POST https://gateway.pixazo.ai/virtual-tryon/v1/r-vton` |
| Type | Synchronous |

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| category | string | Yes | "upper_body", "lower_body", "dresses" |
| garm_img | string | Yes | URL of garment image |
| human_img | string | Yes | URL of person image |
| garment_des | string | No | Garment description |
| crop | boolean | No | Auto-crop (default: true) |
| seed | integer | No | Random seed |
| steps | integer | No | Inference steps (default: 30) |
| force_dc | boolean | No | Force DensePose (default: false) |
| mask_only | boolean | No | Return mask only (default: false) |

<details>
<summary>Python</summary>

```python
import requests

url = "https://gateway.pixazo.ai/virtual-tryon/v1/r-vton"
headers = {
    "Content-Type": "application/json",
    "Ocp-Apim-Subscription-Key": "YOUR_API_KEY"
}
data = {
    "category": "upper_body",
    "garm_img": "https://example.com/tshirt.jpg",
    "human_img": "https://example.com/model.png",
    "crop": True,
    "seed": 40,
    "steps": 30,
    "garment_des": "cute pink top"
}

response = requests.post(url, json=data, headers=headers)
print(response.json())
```
</details>

---

### Additional Virtual Try-On Models

| Model | API ID | Page |
|-------|--------|------|
| FASHN Virtual Try-On v1.6 | `fashn-virtual-try-on-v1-6` | [Link](https://www.pixazo.ai/models/virtual-try-on/fashn-virtual-try-on-v1-6-api) |
| Kling VTON | `kling-vton` | [Link](https://www.pixazo.ai/models/virtual-try-on/kling-api) |
| Pixelforge Accessories VTON | `pixelforge-accessories-vton` | [Link](https://www.pixazo.ai/models/virtual-try-on/pixelforge-accessories-vton-api) |

---

## Lipsync

| Model | Provider | API ID | Description | Page |
|-------|----------|--------|-------------|------|
| ByteDance LatentSync | ByteDance | `bytedance-latentsync` | Audio-to-video lip sync using latent diffusion | [Link](https://www.pixazo.ai/models/lipsync/bytedance-latentsync-api) |
| ByteDance OmniHuman | ByteDance | `bytedance-omni-human` | Full-body human animation from audio | [Link](https://www.pixazo.ai/models/lipsync/bytedance-omni-human-api) |
| Kling Lipsync | Kling AI | `kling-lipsync` | Kling-powered lip synchronization | [Link](https://www.pixazo.ai/models/lipsync/kling-lipsync-api) |
| PixVerse Lipsync | PixVerse | `pixverse-lipsync` | PixVerse lip sync model | [Link](https://www.pixazo.ai/models/lipsync/pixverse-lipsync-api) |
| Sync Lipsync 2 | Sync | `sync-lipsync-2` | Standard lip sync | [Link](https://www.pixazo.ai/models/lipsync/sync-lipsync-2-api) |
| Sync Lipsync 2 Pro | Sync | `sync-lipsync-2-pro` | Pro lip sync with enhanced quality | [Link](https://www.pixazo.ai/models/lipsync/sync-lipsync-2-pro-api) |

---

## Text-to-Speech

| Model | Provider | API ID | Description | Page |
|-------|----------|--------|-------------|------|
| Chatterbox | Resemble AI | `chatterbox` | Expressive text-to-speech | [Link](https://www.pixazo.ai/models/text-to-speech/chatterbox-api) |
| Resemble AI Chatterbox | Resemble AI | `resemble-ai-chatterbox` | High-quality voice synthesis | [Link](https://www.pixazo.ai/models/text-to-speech/resemble-ai-chatterbox-api) |
| Kokoro-82M | Open Source | `kokoro-82m` | Lightweight 82M-param TTS, fast & efficient | [Link](https://www.pixazo.ai/models/text-to-speech/kokoro-82m-api) |
| MiniMax Speech 02 HD | MiniMax | `minimax-speech-02-hd` | HD quality speech synthesis | [Link](https://www.pixazo.ai/models/text-to-speech/minimax-speech-02-hd-api) |
| MiniMax Speech 02 Turbo | MiniMax | `minimax-speech-02-turbo` | Fast speech synthesis | [Link](https://www.pixazo.ai/models/text-to-speech/minimax-speech-02-turbo-api) |
| MiniMax Voice Design | MiniMax | `minimax-voice-design` | Custom voice creation | [Link](https://www.pixazo.ai/models/text-to-speech/minimax-voice-design-api) |
| VibeVoice Realtime 0.5B | VibeVoice | `vibevoice-realtime-0-5b` | Realtime low-latency TTS | [Link](https://www.pixazo.ai/models/text-to-speech/vibevoice-realtime-0-5b-api) |

---

## Audio & Music Generation

| Model | Provider | API ID | Description | Page |
|-------|----------|--------|-------------|------|
| Google Lyria 2 | Google | `google-lyria-2` | Studio-quality 48kHz music from text, genre/tempo/key control | [Link](https://www.pixazo.ai/models/audio-generation/google-lyria-2-api) |
| Meta MusicGen | Meta | `meta-musicgen` | Open-source music generation | [Link](https://www.pixazo.ai/models/audio-generation/meta-musicgen-api) |
| MiniMax Music 01 | MiniMax | `minimax-music-01` | Music generation | [Link](https://www.pixazo.ai/models/audio-generation/minimax-music-01-api) |
| MiniMax Music 2.0 | MiniMax | `minimax-music-2-0` | Advanced music generation | [Link](https://www.pixazo.ai/models/audio-generation/minimax-music-2-0-api) |
| MMAudio | Open Source | `mmaudio` | Multi-modal audio generation | [Link](https://www.pixazo.ai/models/audio-generation/mmaudio-api) |
| Stable Audio 2.5 | Stability AI | `stable-audio-2-5` | Text-to-audio and music | [Link](https://www.pixazo.ai/models/audio-generation/stable-audio-2-5-api) |
| ACE Step | ACE | `ace-step` | Audio generation | [Link](https://www.pixazo.ai/models/ace-step) |
| Tracks | Various | `tracks` | Music tracks | [Link](https://www.pixazo.ai/models/tracks) |
| ElevenLabs | ElevenLabs | `elevenlabs` | Audio synthesis | [Link](https://www.pixazo.ai/models/elevenlabs) |

---

## Voice Cloning

| Model | API ID | Description | Page |
|-------|--------|-------------|------|
| XTTS-v2 | `xtts-v2` | Voice cloning from reference audio | [Link](https://www.pixazo.ai/models/voice-cloning/xtts-v2-api) |

---

## 3D Models

| Model | Provider | API ID | Description | Page |
|-------|----------|--------|-------------|------|
| Hunyuan3D-2 | Tencent | `hunyuan3d-2` | Image/text to textured 3D mesh (PBR-ready) | [Link](https://www.pixazo.ai/models/3d-models/hunyuan3d-2-api) |
| Hunyuan3D-2.1 | Tencent | `hunyuan3d-2-1` | Enhanced 3D generation | [Link](https://www.pixazo.ai/models/3d-models/hunyuan3d-2-1-api) |
| Hunyuan3D-2 MV | Tencent | `hunyuan3d-2mv` | Multi-view 3D generation | [Link](https://www.pixazo.ai/models/3d-models/hunyuan3d-2mv-api) |
| Hunyuan3D-3.0 | Tencent | `hunyuan3d-3-0` | Latest 3D generation | [Link](https://www.pixazo.ai/models/3d-models/hunyuan3d-3-0-api) |
| MVDream | Open Source | `mvdream` | Multi-view diffusion for 3D | [Link](https://www.pixazo.ai/models/3d-models/mvdream-api) |
| Trellis 2 | Trellis | `trellis-2` | 3D generation | [Link](https://www.pixazo.ai/models/3d-models/trellis-2-api) |
| Trellis3D | Trellis | `trellis3d` | 3D modeling | [Link](https://www.pixazo.ai/models/trellis3d) |
| Tripo3D | Tripo | `tripo3d` | 3D generation | [Link](https://www.pixazo.ai/models/tripo3d) |
| Hyper3D | Hyper | `hyper3d` | Fast 3D generation | [Link](https://www.pixazo.ai/models/hyper3d) |

---

## Background Remover

| Model | API ID | Description | Page |
|-------|--------|-------------|------|
| BRIA RMBG 2.0 | `bria-rmbg-2-0` | AI background removal | [Link](https://www.pixazo.ai/models/background-remover/bria-rmbg-2-0-api) |

---

## Tools & Training

| Model | API ID | Description | Page |
|-------|--------|-------------|------|
| Flux 2 Trainer | `flux-2-trainer` | Train custom Flux 2 models | [Link](https://www.pixazo.ai/models/tools/flux-2-trainer-api) |
| Flux LoRA Fast Training | `flux-lora-fast-training` | Fast LoRA fine-tuning for Flux | [Link](https://www.pixazo.ai/models/tools/flux-lora-fast-training-api) |
| LoRA | `lora` | Custom LoRA adapter | [Link](https://www.pixazo.ai/models/lora-api) |
| Qwen Image Edit Plus Trainer | `qwen-image-edit-plus-trainer` | Train Qwen image editing models | [Link](https://www.pixazo.ai/models/tools/qwen-image-edit-plus-trainer-api) |
| Pixelforge Relighting | `pixelforge-relighting` | AI relighting tool | [Link](https://www.pixazo.ai/models/tools/pixelforge-relighting-api) |
| AI Face to Sticker | `ai-face-to-sticker` | Convert faces to stickers | [Link](https://www.pixazo.ai/models/tools/ai-face-to-sticker-api) |
| AI Sticker Maker | `ai-sticker-maker` | Generate AI stickers | [Link](https://www.pixazo.ai/models/tools/ai-sticker-maker-api) |

---

## Additional Brand Pages

These pages list all models from a given provider/brand:

| Brand | Pixazo Page |
|-------|-------------|
| Black Forest Labs (Flux) | [/models/flux](https://www.pixazo.ai/models/flux) |
| Kling AI | [/models/kling](https://www.pixazo.ai/models/kling) |
| MiniMax / Hailuo | [/models/hailuo](https://www.pixazo.ai/models/hailuo) |
| Wan (Alibaba) | [/models/wan](https://www.pixazo.ai/models/wan) |
| Sora (OpenAI) | [/models/sora](https://www.pixazo.ai/models/sora) |
| Veo (Google) | [/models/veo](https://www.pixazo.ai/models/veo) |
| Ideogram | [/models/ideogram](https://www.pixazo.ai/models/ideogram) |
| Qwen | [/models/qwen](https://www.pixazo.ai/models/qwen) |
| LTX | [/models/ltx](https://www.pixazo.ai/models/ltx) |
| Hunyuan | [/models/hunyuan](https://www.pixazo.ai/models/hunyuan) |
| Reve Image | [/models/reve-image](https://www.pixazo.ai/models/reve-image) |
| Seedance | [/models/seedance](https://www.pixazo.ai/models/seedance) |
| Seedream | [/models/seedream](https://www.pixazo.ai/models/seedream) |
| SeedVR | [/models/seedvr](https://www.pixazo.ai/models/seedvr) |
| Runway | [/models/runway](https://www.pixazo.ai/models/runway) |
| Luma | [/models/luma](https://www.pixazo.ai/models/luma) |
| Pika | [/models/pika](https://www.pixazo.ai/models/pika) |
| PixVerse | [/models/pixverse](https://www.pixazo.ai/models/pixverse) |
| DALL-E | [/models/dalle](https://www.pixazo.ai/models/dalle) |
| GPT Image | [/models/gpt-image](https://www.pixazo.ai/models/gpt-image) |
| Grok Imagine | [/models/grok-imagine](https://www.pixazo.ai/models/grok-imagine) |
| Stability AI | [/models/stability-ai](https://www.pixazo.ai/models/stability-ai) |
| SDXL | [/models/sdxl](https://www.pixazo.ai/models/sdxl) |
| Stable Diffusion | [/models/stable-diffusion](https://www.pixazo.ai/models/stable-diffusion) |
| Nano Banana | [/models/nano-banana](https://www.pixazo.ai/models/nano-banana) |
| PixelForge | [/models/pixelforge](https://www.pixazo.ai/models/pixelforge) |
| IDM-VTON | [/models/idm-vton](https://www.pixazo.ai/models/idm-vton) |
| FASHN VTON | [/models/fashn-vton](https://www.pixazo.ai/models/fashn-vton) |
| OmniHuman | [/models/omnihuman](https://www.pixazo.ai/models/omnihuman) |
| Vidu | [/models/vidu](https://www.pixazo.ai/models/vidu) |
| Veed | [/models/veed](https://www.pixazo.ai/models/veed) |
| Mochi | [/models/mochi](https://www.pixazo.ai/models/mochi) |
| Recraft | [/models/recraft](https://www.pixazo.ai/models/recraft) |
| AuraFlow | [/models/auraflow](https://www.pixazo.ai/models/auraflow) |
| Bria | [/models/bria](https://www.pixazo.ai/models/bria) |
| Chatterbox | [/models/chatterbox](https://www.pixazo.ai/models/chatterbox) |
| Crystal Upscaler | [/models/crystal-upscaler](https://www.pixazo.ai/models/crystal-upscaler) |
| ElevenLabs | [/models/elevenlabs](https://www.pixazo.ai/models/elevenlabs) |
| GenFlare | [/models/genflare](https://www.pixazo.ai/models/genflare) |
| Higgsfield | [/models/higgsfield](https://www.pixazo.ai/models/higgsfield) |
| Longcat Image | [/models/longcat-image](https://www.pixazo.ai/models/longcat-image) |
| Lyria | [/models/lyria](https://www.pixazo.ai/models/lyria) |
| P-Video | [/models/p-video](https://www.pixazo.ai/models/p-video) |
| RunDiffusion | [/models/rundiffusion](https://www.pixazo.ai/models/rundiffusion) |
| Studio Ghibli | [/models/studio-ghibli](https://www.pixazo.ai/models/studio-ghibli) |
| Topaz | [/models/topaz](https://www.pixazo.ai/models/topaz) |
| VibeVoice | [/models/vibevoice](https://www.pixazo.ai/models/vibevoice) |
| Qwen Image | [/models/qwen-image](https://www.pixazo.ai/models/qwen-image) |

---

## API ID Quick Reference

| Category | Model | API ID | Gateway Endpoint |
|----------|-------|--------|------------------|
| **Text-to-Image** | Flux Schnell | `flux-1-schnell` | `/flux-1-schnell/v1/getData` |
| | Flux Dev | `flux-dev` | `/flux-dev/v1/getData` |
| | Flux Pro | `flux-pro` | `/flux-pro/v1/getData` |
| | Flux 1.1 Pro Ultra | `flux-1-1-ultra` | `/flux-1-1-ultra/v1/getData` |
| | Flux 2 | `flux-2` | `/flux-2/v1/generate` |
| | Ideogram V2 | `ideogramV_2` | `/ideogramV_2/v1/generate` |
| | Kling AI Image | `kling-ai-image` | `/kling-ai-image/v1/getImageTask` |
| | SDXL | `getImage` | `/getImage/v1/getSDXLImage` |
| | SDXL Lightning | `sdxl_lightning/getImage` | `/sdxl_lightning/getImage/v1/getSDXLImage` |
| | Stable Diffusion | `getImage` | `/getImage/v1/getSDXLImage` |
| | SD Inpainting | `inpainting` | `/inpainting/v1/getImage` |
| **Image-to-Image** | Nano Banana Pro | `nano-banana-pro-770` | `/nano-banana-pro-770/v1/nano-banana-pro-request` |
| **Text-to-Video** | Kling AI T2V | `kling-ai-video` | `/kling-ai-video/v1/generateVideoTask` |
| | MiniMax Hailuo T2V | `minimax-hailuo-ai` | `/minimax-hailuo-ai/v1/generate` |
| **Image-to-Video** | Kling AI I2V | `kling-ai-video` | `/kling-ai-video/v1/getImageToVideoTask` |
| | MiniMax Hailuo I2V | `minimax-hailuo-ai` | `/minimax-hailuo-ai/v1/generate` |
| | Higgsfield DoP | `dop` | `/dop/v1/generate` |
| **Virtual Try-On** | IDM-VTON | `idm-vton-api` | `/idm-vton-api/v1/r-idm-vton` |
| | Kolors VTON | `kling-ai-vton` | `/kling-ai-vton/v1/getVirtualTryOnTask` |
| | Pixelforge Clothing | `virtual-tryon` | `/virtual-tryon/v1/r-vton` |

---

## PawFlow Model ID Mapping

PawFlow tools map to Pixazo models as follows:

| PawFlow Tool | PawFlow Model ID | Pixazo API ID | Pixazo Endpoint |
|-------------|-----------------|---------------|------------------|
| `generate_image` | `flux-schnell` | `flux-1-schnell` | `/flux-1-schnell/v1/getData` |
| `generate_image` | `flux-dev` | `flux-dev` | `/flux-dev/v1/getData` |
| `generate_image` | `flux-pro` | `flux-pro` | `/flux-pro/v1/getData` |
| `generate_image` | `sdxl` | `getImage` | `/getImage/v1/getSDXLImage` |
| `generate_image` | `sdxl-lightning` | `sdxl_lightning/getImage` | `/sdxl_lightning/getImage/v1/getSDXLImage` |
| `generate_image` | `ideogram` | `ideogramV_2` | `/ideogramV_2/v1/generate` |
| `generate_image` | `kling` | `kling-ai-image` | `/kling-ai-image/v1/getImageTask` |
| `edit_image` | `nano-banana-pro` | `nano-banana-pro-770` | `/nano-banana-pro-770/v1/nano-banana-pro-request` |
| `edit_image` | `inpainting` | `inpainting` | `/inpainting/v1/getImage` |
| `generate_video` | `kling` | `kling-ai-video` | `/kling-ai-video/v1/generateVideoTask` |
| `generate_video` | `hailuo` | `minimax-hailuo-ai` | `/minimax-hailuo-ai/v1/generate` |
| `generate_audio` | `lyria` | `google-lyria-2` | see audio section |
| `generate_audio` | `stable-audio` | `stable-audio-2-5` | see audio section |
