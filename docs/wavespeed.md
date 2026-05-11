# WaveSpeedAI API — Model Reference

> Auto-generated from [wavespeed.ai/docs](https://wavespeed.ai/docs).
> 924 API model pages extracted.

## Common API Pattern

- Base URL: `https://api.wavespeed.ai/api/v3`
- Authentication: `Authorization: Bearer ${WAVESPEED_API_KEY}`
- Submit: `POST /api/v3/<model-endpoint>` with JSON parameters
- Poll: `GET data.urls.get` or `GET /api/v3/predictions/{id}/result`
- Terminal statuses: `completed`, `failed`
- Media outputs: `data.outputs[]`

## Counts

### By Category

- `3d`: 19
- `audio`: 54
- `image`: 390
- `lipsync`: 21
- `trainer`: 10
- `try_on`: 2
- `upscale`: 23
- `video`: 400
- `voice_clone`: 5

### By Operation

- `audio_edit`: 6
- `edit_image`: 34
- `frame_to_video`: 13
- `image_to_3d`: 14
- `image_to_video`: 126
- `lipsync`: 20
- `music_generation`: 25
- `reference_to_video`: 17
- `remove_background`: 5
- `speech_to_video`: 1
- `text_to_3d`: 5
- `text_to_image`: 360
- `text_to_speech`: 23
- `text_to_video`: 197
- `train`: 10
- `try_on`: 2
- `upscale`: 18
- `video_edit`: 26
- `video_extend`: 17
- `voice_clone`: 3
- `voice_design`: 2

## Category: 3d

### Hyper3d Rodin V2 Image To 3d

- **Model ID:** `hyper3d/rodin-v2/image-to-3d`
- **Operation:** `image_to_3d`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/hyper3d/rodin-v2/image-to-3d`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/hyper3d/hyper3d-rodin-v2-image-to-3d

**Request Parameters**

- `images`: array Yes [] 1 ~ 5 items Images to be used in generation, up to 5 images. As the form data request will preserve the order of the images, the first image will be the image for material generation.
- `prompt`: string No - A textual prompt to guide the model generation.
- `material`: string No - PBR, All, Shaded The material type.
- `quality_and_mesh`: string No - 4k_Quad, 8k_Quad, 18k_Quad, 50k_Quad, 2K_Triangle, 20K_Triangle, 250K_Triangle, 500K_Triangle The generation quality and mesh mode.
- `geometry_file_format`: string No - glb, fbx, obj, stl, usdz The format of the output geometry file.
- `addons`: string No - HighPack Generate 4K resolution texture instead of the default 2K. If Quad mode, the number of faces will be ~16 times of the number of faces selected in the quality parameter.
- `bbox_condition`: array No - - This is a controlnet that controls the maxmimum sized of the generated model.
- `ta_pose`: boolean No - - Control the generation result to T/A Pose.
- `use_original_alpha`: boolean No - - Used when processing the image.
- `preview_render`: boolean No - - Provided in the download list.
- `seed`: integer No - -1 ~ 2147483647 Seed for random number generator. Set to 0 to use a random seed.

### Hyper3d Rodin V2 Text To 3d

- **Model ID:** `hyper3d/rodin-v2/text-to-3d`
- **Operation:** `text_to_3d`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/hyper3d/rodin-v2/text-to-3d`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/hyper3d/hyper3d-rodin-v2-text-to-3d

**Request Parameters**

- `prompt`: string Yes - A textual prompt to guide the model generation.
- `material`: string No - PBR, All, Shaded The material type.
- `quality_and_mesh`: string No - 4k_Quad, 8k_Quad, 18k_Quad, 50k_Quad, 2K_Triangle, 20K_Triangle, 250K_Triangle, 500K_Triangle The generation quality and mesh mode.
- `geometry_file_format`: string No - glb, fbx, obj, stl, usdz The format of the output geometry file.
- `addons`: string No - HighPack Generate 4K resolution texture instead of the default 2K. If Quad mode, the number of faces will be ~16 times of the number of faces selected in the quality parameter.
- `bbox_condition`: array No - - This is a controlnet that controls the maxmimum sized of the generated model.
- `ta_pose`: boolean No - - Control the generation result to T/A Pose.
- `use_original_alpha`: boolean No - - Used when processing the image.
- `preview_render`: boolean No - - Provided in the download list.
- `seed`: integer No - -1 ~ 2147483647 Seed for random number generator. Set to 0 to use a random seed.

### Tripo3d H3.1 Multiview To 3d

- **Model ID:** `tripo3d/h3.1/image-to-3d`
- **Operation:** `image_to_3d`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/tripo3d/h3.1/image-to-3d`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/tripo3d/tripo3d-h3.1-multiview-to-3d

**Request Parameters**

- `images`: array Yes [] 2 ~ 4 items 2 to 4 image URLs of the same object from different angles. Order: [front, left, back, right]. Front view is required.
- `texture`: boolean No true - Whether to generate textures for the model.
- `pbr`: boolean No true - Whether to generate PBR (Physically Based Rendering) materials. If true, texture is also enabled.
- `texture_quality`: string No standard standard, detailed Quality level for textures. 'detailed' produces higher-resolution textures.
- `geometry_quality`: string No standard standard, detailed Quality level for geometry.
- `texture_alignment`: string No original_image original_image, geometry How textures are aligned. 'original_image' aligns to input image, 'geometry' aligns to generated geometry.
- `auto_size`: boolean No false - Auto-scale the model to real-world dimensions.
- `orientation`: string No default default, align_image Model orientation. 'align_image' auto-rotates to match the input image.
- `quad`: boolean No false - Generate quad (4-sided) mesh topology instead of triangles.
- `face_limit`: integer No - 1000 ~ 2000000 Target number of faces for the generated mesh. If not set, the model adaptively determines the count.
- `model_seed`: integer No - - Seed for geometry generation reproducibility.
- `texture_seed`: integer No - - Seed for texture generation reproducibility.

### Tripo3d H3.1 Image To 3d

- **Model ID:** `tripo3d/h3.1/image-to-3d`
- **Operation:** `image_to_3d`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/tripo3d/h3.1/image-to-3d`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/tripo3d/tripo3d-h3.1-image-to-3d

**Request Parameters**

- `image`: string Yes - URL of the input image for 3D model creation.
- `texture_alignment`: string No original_image original_image, geometry How textures are aligned. 'original_image' aligns to input image, 'geometry' aligns to generated geometry.
- `orientation`: string No default default, align_image Model orientation. 'align_image' auto-rotates to match the input image.
- `texture`: boolean No true - Whether to generate textures for the model.
- `pbr`: boolean No true - Whether to generate PBR (Physically Based Rendering) materials. If true, texture is also enabled.
- `texture_quality`: string No standard standard, detailed Quality level for textures. 'detailed' produces higher-resolution textures.
- `geometry_quality`: string No standard standard, detailed Quality level for geometry.
- `auto_size`: boolean No false - Auto-scale the model to real-world dimensions.
- `quad`: boolean No false - Generate quad (4-sided) mesh topology instead of triangles.
- `face_limit`: integer No - 1000 ~ 2000000 Target number of faces for the generated mesh. If not set, the model adaptively determines the count.
- `model_seed`: integer No - - Seed for geometry generation reproducibility.
- `image_seed`: integer No - - Seed for the text-to-image step.
- `texture_seed`: integer No - - Seed for texture generation reproducibility.

### Tripo3d H3.1 Text To 3d

- **Model ID:** `tripo3d/h3.1/text-to-3d`
- **Operation:** `text_to_3d`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/tripo3d/h3.1/text-to-3d`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/tripo3d/tripo3d-h3.1-text-to-3d

**Request Parameters**

- `prompt`: string Yes - Text description of the 3D object to generate. Maximum 1024 characters.
- `negative_prompt`: string No - Text describing features to avoid in the generated model.Maximum 1024 characters.
- `texture`: boolean No true - Whether to generate textures for the model.
- `pbr`: boolean No true - Whether to generate PBR (Physically Based Rendering) materials. If true, texture is also enabled.
- `texture_quality`: string No standard standard, detailed Quality level for textures. 'detailed' produces higher-resolution textures.
- `geometry_quality`: string No standard standard, detailed Quality level for geometry.
- `auto_size`: boolean No false - Auto-scale the model to real-world dimensions.
- `quad`: boolean No false - Generate quad (4-sided) mesh topology instead of triangles.
- `face_limit`: integer No - 1000 ~ 2000000 Target number of faces for the generated mesh. If not set, the model adaptively determines the count.
- `model_seed`: integer No - - Seed for geometry generation reproducibility.
- `image_seed`: integer No - - Seed for the text-to-image step.
- `texture_seed`: integer No - - Seed for texture generation reproducibility.

### Tripo3d V2.5 Image To 3d

- **Model ID:** `tripo3d/v2.5/image-to-3d`
- **Operation:** `image_to_3d`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/tripo3d/v2.5/image-to-3d`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/tripo3d/tripo3d-v2.5-image-to-3d

**Request Parameters**

- `image`: string Yes - URL of the image to use for model generation.

### Tripo3d V2.5 Multiview To 3d

- **Model ID:** `tripo3d/v2.5/multiview-to-3d`
- **Operation:** `image_to_3d`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/tripo3d/v2.5/multiview-to-3d`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/tripo3d/tripo3d-v2.5-multiview-to-3d

**Request Parameters**

- `front_image_url`: string Yes - - URL of the front image to use for model generation.
- `back_image_url`: string Yes - - URL of the back image to use for model generation.
- `left_image_url`: string Yes - - URL of the left image to use for model generation.
- `right_image_url`: string Yes - - URL of the right image to use for model generation.

### Hunyuan 3d V3.1 Image To 3d Rapid

- **Model ID:** `wavespeed-ai/hunyuan-3d-v3.1/image-to-3d-rapid`
- **Operation:** `image_to_3d`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/hunyuan-3d-v3.1/image-to-3d-rapid`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/hunyuan-3d-v3.1-image-to-3d-rapid

**Request Parameters**

- `image`: string Yes - Front view image of the object to convert into a 3D model (128-5000px, max 8MB, JPG/PNG/WEBP)

### Hunyuan 3d V3.1 Text To 3d Rapid

- **Model ID:** `wavespeed-ai/hunyuan-3d-v3.1/text-to-3d-rapid`
- **Operation:** `text_to_3d`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/hunyuan-3d-v3.1/text-to-3d-rapid`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/hunyuan-3d-v3.1-text-to-3d-rapid

**Request Parameters**

- `prompt`: string Yes - Text description of the 3D content to generate (max 200 UTF-8 characters)

### Hunyuan3d V3 Image To 3d

- **Model ID:** `wavespeed-ai/hunyuan3d-v3/image-to-3d`
- **Operation:** `image_to_3d`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/hunyuan3d-v3/image-to-3d`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/hunyuan3d-v3-image-to-3d

**Request Parameters**

- `image`: string Yes - The URL where the file can be downloaded from.
- `back_image`: string No - - Optional back view image URL for better 3D reconstruction.
- `left_image`: string No - - Optional left view image URL for better 3D reconstruction.
- `right_image`: string No - - Optional right view image URL for better 3D reconstruction.
- `enable_pbr`: boolean No false - Whether to enable PBR material generation
- `polygon_type`: string No triangle triangle, quadrilateral Polygon type. Only takes effect when GenerateType is LowPoly.
- `face_count`: integer No 500000 40000 ~ 1500000 Target face count. Range: 40000-1500000
- `generate_type`: string No Normal Normal, LowPoly, Geometry Generation type. Normal: textured model. LowPoly: polygon reduction. Geometry: white model without texture.

### Hunyuan3d V3 Sketch To 3d

- **Model ID:** `wavespeed-ai/hunyuan3d-v3/sketch-to-3d`
- **Operation:** `image_to_3d`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/hunyuan3d-v3/sketch-to-3d`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/hunyuan3d-v3-sketch-to-3d

**Request Parameters**

- `image`: string Yes - The URL where the file can be downloaded from.
- `prompt`: string Yes - Text description of the 3D content to generate. Supports up to 1024 UTF-8 characters.
- `enable_pbr`: boolean No false - Whether to enable PBR material generation
- `face_count`: integer No 500000 40000 ~ 1500000 Target face count. Range: 40000-1500000

### Hunyuan3d V3 Text To 3d

- **Model ID:** `wavespeed-ai/hunyuan3d-v3/text-to-3d`
- **Operation:** `text_to_3d`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/hunyuan3d-v3/text-to-3d`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/hunyuan3d-v3-text-to-3d

**Request Parameters**

- `prompt`: string Yes - Text description of the 3D content to generate. Supports up to 1024 UTF-8 characters.
- `enable_pbr`: boolean No false - Whether to enable PBR material generation
- `polygon_type`: string No triangle triangle, quadrilateral Polygon type. Only takes effect when GenerateType is LowPoly.
- `face_count`: integer No 500000 40000 ~ 1500000 Target face count. Range: 40000-1500000
- `generate_type`: string No Normal Normal, LowPoly, Geometry Generation type. Normal: textured model. LowPoly: polygon reduction. Geometry: white model without texture.

### Hunyuan3d V2 Base

- **Model ID:** `wavespeed-ai/hunyuan3d/v2-base`
- **Operation:** `image_to_3d`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/hunyuan3d/v2-base`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/hunyuan3d-v2-base

**Request Parameters**

- `image`: string Yes - URL of image to use while generating the 3D model.

### Hunyuan3d V2 Mini

- **Model ID:** `wavespeed-ai/hunyuan3d/v2-mini`
- **Operation:** `image_to_3d`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/hunyuan3d/v2-mini`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/hunyuan3d-v2-mini

**Request Parameters**

- `image`: string Yes - URL of image to use while generating the 3D model.

### Hunyuan3d V2.1

- **Model ID:** `wavespeed-ai/hunyuan3d/v2.1`
- **Operation:** `image_to_3d`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/hunyuan3d/v2.1`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/hunyuan3d-v2.1

**Request Parameters**

- `image`: string Yes - URL of image to use while generating the 3D model.

### Meshy6 Image To 3d

- **Model ID:** `wavespeed-ai/meshy6/image-to-3d`
- **Operation:** `image_to_3d`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/meshy6/image-to-3d`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/meshy6-image-to-3d

**Request Parameters**

- `image`: string Yes - Image URL or base64 data URI for 3D model creation. Supports .jpg, .jpeg, and .png formats.
- `topology`: string No triangle quad, triangle Specify the topology of the generated model. Quad for smooth surfaces, Triangle for detailed geometry.
- `target_polycount`: integer No 30000 100 ~ 300000 Target polygon count in generated model
- `symmetry_mode`: string No auto off, auto, on Controls symmetry behavior of the generated model
- `should_remesh`: boolean No true - Enable remesh phase for cleaner topology
- `should_texture`: boolean No true - Generate textures for the 3D model
- `enable_pbr`: boolean No false - Generate PBR maps (metallic, roughness, normal)
- `ta_pose`: boolean No false - Generate model in A/T pose for rigging
- `texture_prompt`: string No - - Text prompt to guide texturing process
- `texture_image`: string No - - 2D image to guide texturing process

### Meshy6 Text To 3d

- **Model ID:** `wavespeed-ai/meshy6/text-to-3d`
- **Operation:** `text_to_3d`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/meshy6/text-to-3d`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/meshy6-text-to-3d

**Request Parameters**

- `prompt`: string Yes - Describe what kind of object the 3D model is. Maximum 600 characters.
- `art_style`: string No realistic realistic, sculpture Desired artistic style of the generated model
- `topology`: string No triangle quad, triangle Specify the topology of the generated model. Quad for smooth surfaces, Triangle for detailed geometry.
- `target_polycount`: integer No 30000 100 ~ 300000 Target polygon count in generated model
- `symmetry_mode`: string No auto off, auto, on Controls symmetry behavior of the generated model
- `should_remesh`: boolean No true - Enable remesh phase for cleaner topology
- `enable_pbr`: boolean No false - Generate PBR maps (metallic, roughness, normal)
- `ta_pose`: boolean No false - Generate model in A/T pose for rigging
- `enable_prompt_expansion`: boolean No false - Use LLM to expand prompt details for better results
- `texture_prompt`: string No - - Additional text for texture guidance (full mode only)
- `texture_image`: string No - - 2D image for texture guidance (full mode only)

### Sam 3d Body

- **Model ID:** `wavespeed-ai/sam-3d-body`
- **Operation:** `image_to_3d`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/sam-3d-body`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/sam-3d-body

**Request Parameters**

- `image`: string Yes - Input image URL for 3D body generation or segmentation.
- `mask_image`: string No - Optional mask image URL for specific region processing.

### Sam 3d Objects

- **Model ID:** `wavespeed-ai/sam-3d-objects`
- **Operation:** `image_to_3d`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/sam-3d-objects`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/sam-3d-objects

**Request Parameters**

- `image`: string Yes - Input image URL for 3D object generation.
- `prompt`: string No - Text prompt to guide 3D object generation.
- `mask_images`: array No - - Optional array of mask image URLs for specific region processing.


## Category: audio

### Alibaba Qwen3 Tts Flash

- **Model ID:** `alibaba/qwen-image/translate`
- **Operation:** `text_to_speech`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/alibaba/qwen-image/translate`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/alibaba/alibaba-qwen3-tts-flash

**Request Parameters**

- `text`: string Yes - - Text to translate
- `voice`: string Yes Cherry Cherry, Ethan, Nofish, Jennifer, Ryan, Katerina, Elias, Jada, Dylan, Sunny, li, Marcus, Roy, Peter, Rocky, Kiki, Eric Voice name for translation
- `language_type`: string No Auto Auto, Chinese, English, German, Italian, Portuguese, Spanish, Japanese, Korean, French, Russian, Thai Language type for translation

### Elevenlabs Eleven V3

- **Model ID:** `elevenlabs/eleven-v3`
- **Operation:** `text_to_speech`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/elevenlabs/eleven-v3`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/elevenlabs/elevenlabs-eleven-v3

**Request Parameters**

- `text`: string Yes Welcome to our advanced text-to-speech system! Experience high-quality voice synthesis with natural pronunciation and clear articulation. - Text to convert to speech. Every character is 1 token. Maximum 10000 characters. Use <#x#
- `voice_id`: string Yes Alice Aria, Roger, Sarah, Laura, Charlie, George, Callum, River, Liam, Charlotte, Alice, Matilda, Will, Jessica, Eric, Chris, Brian, Daniel, Lily, Bill The voice to use for speech generation
- `similarity`: number No 1 0.00 ~ 1.00 High enhancement boosts overall voice clarity and target speaker similarity. Very high values can cause artifacts, so adjusting this setting to find the optimal value is encouraged.
- `stability`: number No 0.5 0.00 ~ 1.00 Voice stability (0-1) Default value: 0.5
- `use_speaker_boost`: boolean No true - This parameter supports English text normalization, which improves performance in number-reading scenarios.

### Elevenlabs Eleven V3 Timing

- **Model ID:** `elevenlabs/eleven-v3/timing`
- **Operation:** `text_to_speech`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/elevenlabs/eleven-v3/timing`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/elevenlabs/elevenlabs-eleven-v3-timing

**Request Parameters**

- `text`: string Yes Welcome to our advanced text-to-speech system! Experience high-quality voice synthesis with natural pronunciation and clear articulation. - Text to convert to speech. Every character is 1 token. Maximum 10000 characters. Use <#x#
- `voice_id`: string Yes Alice Aria, Roger, Sarah, Laura, Charlie, George, Callum, River, Liam, Charlotte, Alice, Matilda, Will, Jessica, Eric, Chris, Brian, Daniel, Lily, Bill The voice to use for speech generation
- `similarity`: number No 1 0.00 ~ 1.00 High enhancement boosts overall voice clarity and target speaker similarity. Very high values can cause artifacts, so adjusting this setting to find the optimal value is encouraged.
- `stability`: number No 0.5 0.00 ~ 1.00 Voice stability (0-1) Default value: 0.5
- `use_speaker_boost`: boolean No true - This parameter supports English text normalization, which improves performance in number-reading scenarios.

### Elevenlabs Flash V2

- **Model ID:** `elevenlabs/flash-v2`
- **Operation:** `text_to_speech`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/elevenlabs/flash-v2`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/elevenlabs/elevenlabs-flash-v2

**Request Parameters**

- `text`: string Yes - - Text to convert to speech. Every character is 1 token. Maximum 10000 characters. Use <#x#> between words to control pause duration (0.01-99.99s).
- `voice_id`: string Yes Alice Aria, Roger, Sarah, Laura, Charlie, George, Callum, River, Liam, Charlotte, Alice, Matilda, Will, Jessica, Eric, Chris, Brian, Daniel, Lily, Bill The voice to use for speech generation
- `similarity`: number No 1 0.00 ~ 1.00 High enhancement boosts overall voice clarity and target speaker similarity. Very high values can cause artifacts, so adjusting this setting to find the optimal value is encouraged.
- `stability`: number No 0.5 0.00 ~ 1.00 Voice stability (0-1) Default value: 0.5
- `use_speaker_boost`: boolean No true - This parameter supports English text normalization, which improves performance in number-reading scenarios.

### Elevenlabs Flash V2.5

- **Model ID:** `elevenlabs/flash-v2.5`
- **Operation:** `text_to_speech`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/elevenlabs/flash-v2.5`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/elevenlabs/elevenlabs-flash-v2.5

**Request Parameters**

- `text`: string Yes - - Text to convert to speech. Every character is 1 token. Maximum 10000 characters. Use <#x#> between words to control pause duration (0.01-99.99s).
- `voice_id`: string Yes Alice Aria, Roger, Sarah, Laura, Charlie, George, Callum, River, Liam, Charlotte, Alice, Matilda, Will, Jessica, Eric, Chris, Brian, Daniel, Lily, Bill The voice to use for speech generation. Custom values are available
- `similarity`: number No 1 0.00 ~ 1.00 High enhancement boosts overall voice clarity and target speaker similarity. Very high values can cause artifacts, so adjusting this setting to find the optimal value is encouraged.
- `stability`: number No 0.5 0.00 ~ 1.00 Voice stability (0-1) Default value: 0.5
- `use_speaker_boost`: boolean No true - This parameter supports English text normalization, which improves performance in number-reading scenarios.

### Elevenlabs Music

- **Model ID:** `elevenlabs/music`
- **Operation:** `music_generation`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/elevenlabs/music`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/elevenlabs/elevenlabs-music

**Request Parameters**

- `prompt`: string Yes - Text description of the music you want to generate.
- `music_length_ms`: integer No 10000 5000 ~ 300000 Target duration in milliseconds (5,000-300,000ms, i.e., 5 seconds to 5 minutes).
- `force_instrumental`: boolean No true - Generate instrumental music without vocals.
- `output_format`: string No mp3_standard mp3_standard, mp3_high_quality, wav_16khz, wav_22khz, wav_24khz, wav_cd_quality Audio output format and quality.

### Elevenlabs Turbo V2

- **Model ID:** `elevenlabs/turbo-v2`
- **Operation:** `text_to_speech`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/elevenlabs/turbo-v2`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/elevenlabs/elevenlabs-turbo-v2

**Request Parameters**

- `text`: string Yes - - Text to convert to speech. Every character is 1 token. Maximum 10000 characters. Use <#x#> between words to control pause duration (0.01-99.99s).
- `voice_id`: string Yes Alice Aria, Roger, Sarah, Laura, Charlie, George, Callum, River, Liam, Charlotte, Alice, Matilda, Will, Jessica, Eric, Chris, Brian, Daniel, Lily, Bill The voice to use for speech generation
- `similarity`: number No 1 0.00 ~ 1.00 High enhancement boosts overall voice clarity and target speaker similarity. Very high values can cause artifacts, so adjusting this setting to find the optimal value is encouraged.
- `stability`: number No 0.5 0.00 ~ 1.00 Voice stability (0-1) Default value: 0.5
- `use_speaker_boost`: boolean No true - This parameter supports English text normalization, which improves performance in number-reading scenarios.

### Elevenlabs Turbo V2.5

- **Model ID:** `elevenlabs/turbo-v2.5`
- **Operation:** `text_to_speech`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/elevenlabs/turbo-v2.5`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/elevenlabs/elevenlabs-turbo-v2.5

**Request Parameters**

- `text`: string Yes Welcome to our advanced text-to-speech system! Experience high-quality voice synthesis with natural pronunciation and clear articulation. - Text to convert to speech. Every character is 1 token. Maximum 10000 characters. Use <#x#
- `voice_id`: string Yes Alice Aria, Roger, Sarah, Laura, Charlie, George, Callum, River, Liam, Charlotte, Alice, Matilda, Will, Jessica, Eric, Chris, Brian, Daniel, Lily, Bill The voice to use for speech generation
- `similarity`: number No 1 0.00 ~ 1.00 High enhancement boosts overall voice clarity and target speaker similarity. Very high values can cause artifacts, so adjusting this setting to find the optimal value is encouraged.
- `stability`: number No 0.5 0.00 ~ 1.00 Voice stability (0-1) Default value: 0.5
- `use_speaker_boost`: boolean No true - This parameter supports English text normalization, which improves performance in number-reading scenarios.

### Elevenlabs Voice Changer

- **Model ID:** `elevenlabs/voice-changer`
- **Operation:** `music_generation`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/elevenlabs/voice-changer`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/elevenlabs/elevenlabs-voice-changer

**Request Parameters**

- `audio`: string Yes - - URL of the audio file to transform
- `voice_id`: string No Alice Alice, Aria, Bill, Brian, Callum, Charlie, Charlotte, Chris, Daniel, Eric, George, Jessica, Laura, Liam, Lily, Matilda, River, Roger, Sarah, Will Voice to apply to the audio
- `remove_background_noise`: boolean No false - Remove background noise from the audio

### Google Gemini 2.5 Flash Text To Speech

- **Model ID:** `google/gemini-2.5-flash/text-to-speech`
- **Operation:** `text_to_speech`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/google/gemini-2.5-flash/text-to-speech`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/google/google-gemini-2.5-flash-text-to-speech

**Request Parameters**

- `text`: string Yes - - Styling instructions on how to synthesize the content in the text field.Less than or equal to 8,000 bytes
- `language`: string Yes English (United States) Arabic (Egypt), Bangla (Bangladesh), Dutch (Netherlands), English (India), English (United States), French (France), German (Germany), Hindi (India), Indonesian (Indonesia), Italian (Italy), Japanese (Japa
- `speakers`: array Yes [{"speaker":"","voice":"Achernar"}] 1 ~ 2 items Array of terminoogies to use for translation

### Google Gemini 2.5 Pro Text To Speech

- **Model ID:** `google/gemini-2.5-pro/text-to-speech`
- **Operation:** `text_to_speech`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/google/gemini-2.5-pro/text-to-speech`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/google/google-gemini-2.5-pro-text-to-speech

**Request Parameters**

- `text`: string Yes - - Styling instructions on how to synthesize the content in the text field.Less than or equal to 8,000 bytes
- `language`: string Yes English (United States) Arabic (Egypt), Bangla (Bangladesh), Dutch (Netherlands), English (India), English (United States), French (France), German (Germany), Hindi (India), Indonesian (Indonesia), Italian (Italy), Japanese (Japa
- `speakers`: array Yes [{"speaker":"","voice":"Achernar"}] 1 ~ 2 items Array of terminoogies to use for translation

### Google Lyria 3 Clip Music

- **Model ID:** `google/lyria-3-clip/music`
- **Operation:** `music_generation`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/google/lyria-3-clip/music`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/google/google-lyria-3-clip-music

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `image`: string No - The image for generating the output.
- `negative_prompt`: string No - A description of what to exclude from the generated audio.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Google Lyria 3 Pro Music

- **Model ID:** `google/lyria-3-pro/music`
- **Operation:** `music_generation`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/google/lyria-3-pro/music`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/google/google-lyria-3-pro-music

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `image`: string No - The image for generating the output.
- `negative_prompt`: string No - A description of what to exclude from the generated audio.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Inworld Inworld 1.5 Max Text To Speech

- **Model ID:** `inworld/inworld-1.5-max/text-to-speech`
- **Operation:** `text_to_speech`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/inworld/inworld-1.5-max/text-to-speech`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/inworld/inworld-inworld-1.5-max-text-to-speech

**Request Parameters**

- `text`: string Yes - - Styling instructions on how to synthesize the content in the text field.
- `voice_id`: string No Alex Alex, Ashley, Craig, Deborah, Dennis, Edward, Elizabeth, Hades, Julia, Pixie, Mark, Olivia, Priya, Ronald, Sarah, Shaun, Theodore, Timothy, Wendy, Dominus, Hana, Clive, Carter, Blake, Luna, Yichen, Xiaoyin, Xinyi, Jing, Erik,
- `speaking_rate`: number No 1 0.5 ~ 1.5 The speed of speaking.
- `temperature`: number No 1 0.7 ~ 1.5 The temperature to use for the generation. A higher value means more randomness in the output.

### Inworld Inworld 1.5 Mini Text To Speech

- **Model ID:** `inworld/inworld-1.5-mini/text-to-speech`
- **Operation:** `text_to_speech`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/inworld/inworld-1.5-mini/text-to-speech`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/inworld/inworld-inworld-1.5-mini-text-to-speech

**Request Parameters**

- `text`: string Yes - - Styling instructions on how to synthesize the content in the text field.
- `voice_id`: string No Alex Alex, Ashley, Craig, Deborah, Dennis, Edward, Elizabeth, Hades, Julia, Pixie, Mark, Olivia, Priya, Ronald, Sarah, Shaun, Theodore, Timothy, Wendy, Dominus, Hana, Clive, Carter, Blake, Luna, Yichen, Xiaoyin, Xinyi, Jing, Erik,
- `speaking_rate`: number No 1 0.5 ~ 1.5 The speed of speaking.
- `temperature`: number No 1 0.7 ~ 1.5 The temperature to use for the generation. A higher value means more randomness in the output.

### Inworld Realtime Tts 2

- **Model ID:** `inworld/realtime-tts-2`
- **Operation:** `text_to_speech`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/inworld/realtime-tts-2`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/inworld/inworld-realtime-tts-2

**Request Parameters**

- `text`: string Yes - - Text to synthesize into speech. Maximum input of 2,000 characters.
- `voice_id`: string No Dennis Alex, Ashley, Craig, Deborah, Dennis, Edward, Elizabeth, Hades, Julia, Pixie, Mark, Olivia, Priya, Ronald, Sarah, Shaun, Theodore, Timothy, Wendy, Dominus, Hana, Clive, Carter, Blake, Luna, Yichen, Xiaoyin, Xinyi, Jing, Eri
- `speaking_rate`: number No 1 0.5 ~ 1.5 The speed of speaking.
- `temperature`: number No 1 0.7 ~ 1.5 The temperature to use for the generation. A higher value means more randomness in the output.
- `output_format`: string No MP3 MP3, LINEAR16, OGG_OPUS, FLAC, WAV Output audio format.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Kwaivgi Kling Text To Audio

- **Model ID:** `kwaivgi/kling-text-to-audio`
- **Operation:** `music_generation`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/kwaivgi/kling-text-to-audio`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/kwaivgi/kwaivgi-kling-text-to-audio

**Request Parameters**

- `prompt`: string Yes - Text prompt for audio generation, maximum 200 characters
- `duration`: number Yes 10 3 ~ 10 Duration of the generated audio in seconds, range: 3.0 to 10.0 seconds, supports one decimal place

### Kwaivgi Kling V1 Tts

- **Model ID:** `kwaivgi/kling-v1-tts`
- **Operation:** `text_to_speech`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/kwaivgi/kling-v1-tts`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/kwaivgi/kwaivgi-kling-v1-tts

**Request Parameters**

- `text`: string Yes - - The text to be converted to speech. the max length is 512 characters.
- `voice_id`: string Yes genshin_vindi2 genshin_vindi2, zhinen_xuesheng, AOT, ai_shatang, genshin_klee2, genshin_kirara, ai_kaiya, oversea_male1, ai_chenjiahao_712, girlfriend_4_speech02, chat1_female_new-3, chat_0407_5-1, cartoon-boy-07, uk_boy1, cartoo
- `speed`: number No 1 0.8 ~ 2.0 Speech speed. Range: 0.8-2.0, where 1.0 is normal speed.

### Kwaivgi Kling V2.6 Create Voice

- **Model ID:** `kwaivgi/kling-v2.6/create-voice`
- **Operation:** `music_generation`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/kwaivgi/kling-v2.6/create-voice`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/kwaivgi/kwaivgi-kling-v2.6-create-voice

**Request Parameters**

- `audio`: string Yes - - The voice needs to be clean and free of noise, with only one type of human voice present, with a duration of no less than 5 seconds and no longer than 30 seconds.

### Microsoft Vibevoice

- **Model ID:** `microsoft/vibevoice`
- **Operation:** `music_generation`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/microsoft/vibevoice`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/microsoft/microsoft-vibevoice

**Request Parameters**

- `prompt`: string Yes - Text to convert to speech. For multi-speaker dialogue, use 'Speaker 0:', 'Speaker 1:' prefixes.
- `speaker_1`: string No en-Alice_woman en-Alice_woman, en-Carter_man, en-Frank_man, en-Mary_woman_bgm, en-Maya_woman, in-Samuel_man, zh-Anchen_man_bgm, zh-Bowen_man, zh-Xinran_woman Voice for Speaker 0.
- `speaker_2`: string No - en-Alice_woman, en-Carter_man, en-Frank_man, en-Mary_woman_bgm, en-Maya_woman, in-Samuel_man, zh-Anchen_man_bgm, zh-Bowen_man, zh-Xinran_woman Voice for Speaker 1 (optional).
- `speaker_3`: string No - en-Alice_woman, en-Carter_man, en-Frank_man, en-Mary_woman_bgm, en-Maya_woman, in-Samuel_man, zh-Anchen_man_bgm, zh-Bowen_man, zh-Xinran_woman Voice for Speaker 2 (optional).
- `speaker_4`: string No - en-Alice_woman, en-Carter_man, en-Frank_man, en-Mary_woman_bgm, en-Maya_woman, in-Samuel_man, zh-Anchen_man_bgm, zh-Bowen_man, zh-Xinran_woman Voice for Speaker 3 (optional).
- `scale`: number No 1.3 1 ~ 2 CFG Scale (Guidance Strength).

### Minimax Music 01

- **Model ID:** `minimax/music-01`
- **Operation:** `music_generation`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/minimax/music-01`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/minimax/minimax-music-01

**Request Parameters**

- `lyrics`: string No - - Lyrics with optional formatting. You can use a newline to separate each line of lyrics. You can use two newlines to add a pause between lines. You can use double hash marks (##) at the beginning and end of the lyrics to add ac
- `bitrate`: integer No 256000 32000, 64000, 128000, 256000 Bitrate for the generated music
- `sample_rate`: integer No 44100 16000, 24000, 32000, 44100 Sample rate for the generated music
- `song`: string No - - Reference song, should contain music and vocals. Must be a .wav or .mp3 file longer than 15 seconds.
- `voice`: string No - - Voice reference. Must be a .wav or .mp3 file longer than 15 seconds. If only a voice reference is given, an a cappella vocal hum will be generated.
- `instrumental`: string No - - Instrumental reference. Must be a .wav or .mp3 file longer than 15 seconds. If only an instrumental reference is given, a track without vocals will be generated.

### Minimax Music 02

- **Model ID:** `minimax/music-02`
- **Operation:** `music_generation`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/minimax/music-02`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/minimax/minimax-music-02

**Request Parameters**

- `prompt`: string Yes - Prompt for the music generation.
- `lyrics`: string Yes - - Lyrics with optional formatting. You can use a newline to separate each line of lyrics. You can use two newlines to add a pause between lines. You can use double hash marks (##) at the beginning and end of the lyrics to add a
- `bitrate`: integer No 256000 60000, 32000, 64000, 128000, 256000 Bitrate for the generated music
- `sample_rate`: integer No 44100 16000, 24000, 32000, 44100 Sample rate for the generated music

### Minimax Music 2.5

- **Model ID:** `minimax/music-2.5`
- **Operation:** `music_generation`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/minimax/music-2.5`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/minimax/minimax-music-2.5

**Request Parameters**

- `prompt`: string Yes - Prompt for the music generation.
- `lyrics`: string Yes - - Lyrics with optional formatting. You can use a newline to separate each line of lyrics. You can use two newlines to add a pause between lines. You can use double hash marks (##) at the beginning and end of the lyrics to add a
- `bitrate`: integer No 256000 60000, 32000, 64000, 128000, 256000 Bitrate for the generated music
- `sample_rate`: integer No 44100 16000, 24000, 32000, 44100 Sample rate for the generated music

### Minimax Music 2.6

- **Model ID:** `minimax/music-2.6`
- **Operation:** `music_generation`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/minimax/music-2.6`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/minimax/minimax-music-2.6

**Request Parameters**

- `prompt`: string Yes - Prompt for the music generation.
- `lyrics`: string Yes - - Lyrics with optional formatting. You can use a newline to separate each line of lyrics. You can use two newlines to add a pause between lines. You can use double hash marks (##) at the beginning and end of the lyrics to add a
- `bitrate`: integer No 256000 60000, 32000, 64000, 128000, 256000 Bitrate for the generated music
- `sample_rate`: integer No 44100 16000, 24000, 32000, 44100 Sample rate for the generated music
- `is_instrumental`: boolean No false - Whether to generate instrumental music (no vocals).

### Minimax Music Cover

- **Model ID:** `minimax/music-cover`
- **Operation:** `music_generation`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/minimax/music-cover`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/minimax/minimax-music-cover

**Request Parameters**

- `prompt`: string Yes - Target style description for the cover, 10-300 characters. Example: 'R&B Neo-Soul: warm tenor, Rhodes piano, smooth groove, late-night vibe'.
- `audio`: string Yes - - URL of the reference song in MP3 format, between 6 seconds and 6 minutes. Music-cover currently only supports audio with vocals.

### Minimax Music V1.5

- **Model ID:** `minimax/music-v1.5`
- **Operation:** `music_generation`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/minimax/music-v1.5`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/minimax/minimax-music-v1.5

**Request Parameters**

- `lyrics_prompt`: string Yes blues, melancholic, raw, lonely bar, heartbreak. - Control music generation by inputting a text prompt. Valid input: 10-300 characters.
- `prompt`: string Yes - The positive prompt for the generation.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Minimax Speech 02 Hd

- **Model ID:** `minimax/speech-02-hd`
- **Operation:** `text_to_speech`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/minimax/speech-02-hd`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/minimax/minimax-speech-02-hd

**Request Parameters**

- `text`: string Yes Welcome to our advanced text-to-speech system! Experience high-quality voice synthesis with natural pronunciation and clear articulation. - Text to convert to speech. Every character is 1 token. Maximum 10000 characters. Use <#x#
- `voice_id`: string Yes - Wise_Woman, Friendly_Person, Inspirational_girl, Deep_Voice_Man, Calm_Woman, Casual_Guy, Lively_Girl, Patient_Man, Young_Knight, Determined_Man, Lovely_Girl, Decent_Boy, Imposing_Manner, Elegant_Man, Abbess, Sweet_Girl_2, Exube
- `speed`: number No 1 0.50 ~ 2.00 Speech speed. Range: 0.5-2.0, where 1.0 is normal speed.
- `volume`: number No 1 0.10 ~ 10.00 Speech volume. Range: 0.1-10.0, where 1.0 is normal volume.
- `pitch`: number No - -12 ~ 12 Speech pitch. Range: -12 to 12, where 0 is normal pitch.
- `emotion`: string No happy happy, sad, angry, fearful, disgusted, surprised, neutral The emotion of the generated speech.
- `english_normalization`: boolean No false - This parameter supports English text normalization, which improves performance in number-reading scenarios.
- `sample_rate`: integer No - 8000, 16000, 22050, 24000, 32000, 44100 Sample rate of generated sound.
- `bitrate`: integer No - 32000, 64000, 128000, 256000 Bitrate of generated sound.
- `channel`: string No - 1, 2 The number of channels of the generated audio. 1: mono, 2: stereo.
- `format`: string No - mp3, wav, pcm, flac Format of generated sound.
- `language_boost`: string No - Chinese, Chinese,Yue, English, Arabic, Russian, Spanish, French, Portuguese, German, Turkish, Dutch, Ukrainian, Vietnamese, Indonesian, Japanese, Italian, Korean, Thai, Polish, Romanian, Greek, Czech, Finnish, Hindi, auto Enhanc
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Minimax Speech 02 Turbo

- **Model ID:** `minimax/speech-02-turbo`
- **Operation:** `text_to_speech`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/minimax/speech-02-turbo`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/minimax/minimax-speech-02-turbo

**Request Parameters**

- `text`: string Yes Hello world! This is a test of the text-to-speech system. - Text to convert to speech. Every character is 1 token. Maximum 10000 characters. Use <#x#> between words to control pause duration (0.01-99.99s).
- `voice_id`: string Yes - Wise_Woman, Friendly_Person, Inspirational_girl, Deep_Voice_Man, Calm_Woman, Casual_Guy, Lively_Girl, Patient_Man, Young_Knight, Determined_Man, Lovely_Girl, Decent_Boy, Imposing_Manner, Elegant_Man, Abbess, Sweet_Girl_2, Exube
- `speed`: number No 1 0.50 ~ 2.00 Speech speed. Range: 0.5-2.0, where 1.0 is normal speed.
- `volume`: number No 1 0.10 ~ 10.00 Speech volume. Range: 0.1-10.0, where 1.0 is normal volume.
- `pitch`: number No - -12 ~ 12 Speech pitch. Range: -12 to 12, where 0 is normal pitch.
- `emotion`: string No happy happy, sad, angry, fearful, disgusted, surprised, neutral The emotion of the generated speech.
- `english_normalization`: boolean No false - This parameter supports English text normalization, which improves performance in number-reading scenarios.
- `sample_rate`: integer No - 8000, 16000, 22050, 24000, 32000, 44100 Sample rate of generated sound.
- `bitrate`: integer No - 32000, 64000, 128000, 256000 Bitrate of generated sound.
- `channel`: string No - 1, 2 The number of channels of the generated audio. 1: mono, 2: stereo.
- `format`: string No - mp3, wav, pcm, flac Format of generated sound.
- `language_boost`: string No - Chinese, Chinese,Yue, English, Arabic, Russian, Spanish, French, Portuguese, German, Turkish, Dutch, Ukrainian, Vietnamese, Indonesian, Japanese, Italian, Korean, Thai, Polish, Romanian, Greek, Czech, Finnish, Hindi, auto Enhanc
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Minimax Speech 2.5 Hd Preview

- **Model ID:** `minimax/speech-2.5-hd-preview`
- **Operation:** `text_to_speech`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/minimax/speech-2.5-hd-preview`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/minimax/minimax-speech-2.5-hd-preview

**Request Parameters**

- `text`: string Yes - - Text to convert to speech. Every character is 1 token. Maximum 10000 characters. Use <#x#> between words to control pause duration (0.01-99.99s).
- `voice_id`: string Yes - Wise_Woman, Friendly_Person, Inspirational_girl, Deep_Voice_Man, Calm_Woman, Casual_Guy, Lively_Girl, Patient_Man, Young_Knight, Determined_Man, Lovely_Girl, Decent_Boy, Imposing_Manner, Elegant_Man, Abbess, Sweet_Girl_2, Exube
- `speed`: number No 1 0.50 ~ 2.00 Speech speed. Range: 0.5-2.0, where 1.0 is normal speed.
- `volume`: number No 1 0.10 ~ 10.00 Speech volume. Range: 0.1-10.0, where 1.0 is normal volume.
- `pitch`: number No - -12 ~ 12 Speech pitch. Range: -12 to 12, where 0 is normal pitch.
- `emotion`: string No happy happy, sad, angry, fearful, disgusted, surprised, neutral The emotion of the generated speech.
- `english_normalization`: boolean No false - This parameter supports English text normalization, which improves performance in number-reading scenarios.
- `sample_rate`: integer No - 8000, 16000, 22050, 24000, 32000, 44100 Sample rate of generated sound.
- `bitrate`: integer No - 32000, 64000, 128000, 256000 Bitrate of generated sound.
- `channel`: string No - 1, 2 The number of channels of the generated audio. 1: mono, 2: stereo.
- `format`: string No - mp3, wav, pcm, flac Format of generated sound.
- `language_boost`: string No - Chinese, Chinese,Yue, English, Arabic, Russian, Spanish, French, Portuguese, German, Turkish, Dutch, Ukrainian, Vietnamese, Indonesian, Japanese, Italian, Korean, Thai, Polish, Romanian, Greek, Czech, Finnish, Hindi, Bulgarian, 
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Minimax Speech 2.5 Turbo Preview

- **Model ID:** `minimax/speech-2.5-turbo-preview`
- **Operation:** `text_to_speech`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/minimax/speech-2.5-turbo-preview`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/minimax/minimax-speech-2.5-turbo-preview

**Request Parameters**

- `text`: string Yes - - Text to convert to speech. Every character is 1 token. Maximum 10000 characters. Use <#x#> between words to control pause duration (0.01-99.99s).
- `voice_id`: string Yes - Wise_Woman, Friendly_Person, Inspirational_girl, Deep_Voice_Man, Calm_Woman, Casual_Guy, Lively_Girl, Patient_Man, Young_Knight, Determined_Man, Lovely_Girl, Decent_Boy, Imposing_Manner, Elegant_Man, Abbess, Sweet_Girl_2, Exube
- `speed`: number No 1 0.50 ~ 2.00 Speech speed. Range: 0.5-2.0, where 1.0 is normal speed.
- `volume`: number No 1 0.10 ~ 10.00 Speech volume. Range: 0.1-10.0, where 1.0 is normal volume.
- `pitch`: number No - -12 ~ 12 Speech pitch. Range: -12 to 12, where 0 is normal pitch.
- `emotion`: string No happy happy, sad, angry, fearful, disgusted, surprised, neutral The emotion of the generated speech.
- `english_normalization`: boolean No false - This parameter supports English text normalization, which improves performance in number-reading scenarios.
- `sample_rate`: integer No - 8000, 16000, 22050, 24000, 32000, 44100 Sample rate of generated sound.
- `bitrate`: integer No - 32000, 64000, 128000, 256000 Bitrate of generated sound.
- `channel`: string No - 1, 2 The number of channels of the generated audio. 1: mono, 2: stereo.
- `format`: string No - mp3, wav, pcm, flac Format of generated sound.
- `language_boost`: string No - Chinese, Chinese,Yue, English, Arabic, Russian, Spanish, French, Portuguese, German, Turkish, Dutch, Ukrainian, Vietnamese, Indonesian, Japanese, Italian, Korean, Thai, Polish, Romanian, Greek, Czech, Finnish, Hindi, Bulgarian, 
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Minimax Speech 2.6 Hd

- **Model ID:** `minimax/speech-2.6-hd`
- **Operation:** `text_to_speech`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/minimax/speech-2.6-hd`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/minimax/minimax-speech-2.6-hd

**Request Parameters**

- `text`: string Yes - - Text to convert to speech. Every character is 1 token. Maximum 10000 characters. Use <#x#> between words to control pause duration (0.01-99.99s).
- `voice_id`: string Yes - Wise_Woman, Friendly_Person, Inspirational_girl, Deep_Voice_Man, Calm_Woman, Casual_Guy, Lively_Girl, Patient_Man, Young_Knight, Determined_Man, Lovely_Girl, Decent_Boy, Imposing_Manner, Elegant_Man, Abbess, Sweet_Girl_2, Exube
- `speed`: number No 1 0.50 ~ 2.00 Speech speed. Range: 0.5-2.0, where 1.0 is normal speed.
- `volume`: number No 1 0.10 ~ 10.00 Speech volume. Range: 0.1-10.0, where 1.0 is normal volume.
- `pitch`: number No - -12 ~ 12 Speech pitch. Range: -12 to 12, where 0 is normal pitch.
- `emotion`: string No happy happy, sad, angry, fearful, disgusted, surprised, neutral The emotion of the generated speech.
- `english_normalization`: boolean No false - This parameter supports English text normalization, which improves performance in number-reading scenarios.
- `sample_rate`: integer No - 8000, 16000, 22050, 24000, 32000, 44100 Sample rate of generated sound.
- `bitrate`: integer No - 32000, 64000, 128000, 256000 Bitrate of generated sound.
- `channel`: string No - 1, 2 The number of channels of the generated audio. 1: mono, 2: stereo.
- `format`: string No - mp3, wav, pcm, flac Format of generated sound.
- `language_boost`: string No - Chinese, Chinese,Yue, English, Arabic, Russian, Spanish, French, Portuguese, German, Turkish, Dutch, Ukrainian, Vietnamese, Indonesian, Japanese, Italian, Korean, Thai, Polish, Romanian, Greek, Czech, Finnish, Hindi, Bulgarian, 
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Minimax Speech 2.6 Turbo

- **Model ID:** `minimax/speech-2.6-turbo`
- **Operation:** `text_to_speech`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/minimax/speech-2.6-turbo`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/minimax/minimax-speech-2.6-turbo

**Request Parameters**

- `text`: string Yes - - Text to convert to speech. Every character is 1 token. Maximum 10000 characters. Use <#x#> between words to control pause duration (0.01-99.99s).
- `voice_id`: string Yes - Wise_Woman, Friendly_Person, Inspirational_girl, Deep_Voice_Man, Calm_Woman, Casual_Guy, Lively_Girl, Patient_Man, Young_Knight, Determined_Man, Lovely_Girl, Decent_Boy, Imposing_Manner, Elegant_Man, Abbess, Sweet_Girl_2, Exube
- `speed`: number No 1 0.50 ~ 2.00 Speech speed. Range: 0.5-2.0, where 1.0 is normal speed.
- `volume`: number No 1 0.10 ~ 10.00 Speech volume. Range: 0.1-10.0, where 1.0 is normal volume.
- `pitch`: number No - -12 ~ 12 Speech pitch. Range: -12 to 12, where 0 is normal pitch.
- `emotion`: string No happy happy, sad, angry, fearful, disgusted, surprised, neutral The emotion of the generated speech.
- `english_normalization`: boolean No false - This parameter supports English text normalization, which improves performance in number-reading scenarios.
- `sample_rate`: integer No - 8000, 16000, 22050, 24000, 32000, 44100 Sample rate of generated sound.
- `bitrate`: integer No - 32000, 64000, 128000, 256000 Bitrate of generated sound.
- `channel`: string No - 1, 2 The number of channels of the generated audio. 1: mono, 2: stereo.
- `format`: string No - mp3, wav, pcm, flac Format of generated sound.
- `language_boost`: string No - Chinese, Chinese,Yue, English, Arabic, Russian, Spanish, French, Portuguese, German, Turkish, Dutch, Ukrainian, Vietnamese, Indonesian, Japanese, Italian, Korean, Thai, Polish, Romanian, Greek, Czech, Finnish, Hindi, Bulgarian, 
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Minimax Speech 2.8 Hd

- **Model ID:** `minimax/speech-2.8-hd`
- **Operation:** `text_to_speech`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/minimax/speech-2.8-hd`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/minimax/minimax-speech-2.8-hd

**Request Parameters**

- `text`: string Yes - - Text to convert to speech. Every character is 1 token. Maximum 10000 characters. Use <#x#> between words to control pause duration (0.01-99.99s).
- `pronunciation_dict`: array No [] - Format: Alias/Pronunciation, e.g. Omg/Oh my god
- `voice_id`: string Yes - Wise_Woman, Friendly_Person, Inspirational_girl, Deep_Voice_Man, Calm_Woman, Casual_Guy, Lively_Girl, Patient_Man, Young_Knight, Determined_Man, Lovely_Girl, Decent_Boy, Imposing_Manner, Elegant_Man, Abbess, Sweet_Girl_2, Exube
- `speed`: number No 1 0.50 ~ 2.00 Speech speed. Range: 0.5-2.0, where 1.0 is normal speed.
- `volume`: number No 1 0.10 ~ 10.00 Speech volume. Range: 0.1-10.0, where 1.0 is normal volume.
- `pitch`: number No - -12 ~ 12 Speech pitch. Range: -12 to 12, where 0 is normal pitch.
- `emotion`: string No happy happy, sad, angry, fearful, disgusted, surprised, neutral The emotion of the generated speech.
- `english_normalization`: boolean No false - This parameter supports English text normalization, which improves performance in number-reading scenarios.
- `sample_rate`: integer No - 8000, 16000, 22050, 24000, 32000, 44100 Sample rate of generated sound.
- `bitrate`: integer No - 32000, 64000, 128000, 256000 Bitrate of generated sound.
- `channel`: string No - 1, 2 The number of channels of the generated audio. 1: mono, 2: stereo.
- `format`: string No - mp3, wav, pcm, flac Format of generated sound.
- `language_boost`: string No - Chinese, Chinese,Yue, English, Arabic, Russian, Spanish, French, Portuguese, German, Turkish, Dutch, Ukrainian, Vietnamese, Indonesian, Japanese, Italian, Korean, Thai, Polish, Romanian, Greek, Czech, Finnish, Hindi, Bulgarian, 
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Minimax Speech 2.8 Turbo

- **Model ID:** `minimax/speech-2.8-turbo`
- **Operation:** `text_to_speech`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/minimax/speech-2.8-turbo`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/minimax/minimax-speech-2.8-turbo

**Request Parameters**

- `text`: string Yes - - Text to convert to speech. Every character is 1 token. Maximum 10000 characters. Use <#x#> between words to control pause duration (0.01-99.99s).
- `pronunciation_dict`: array No [] - Format: Alias/Pronunciation, e.g. Omg/Oh my god
- `voice_id`: string Yes - Wise_Woman, Friendly_Person, Inspirational_girl, Deep_Voice_Man, Calm_Woman, Casual_Guy, Lively_Girl, Patient_Man, Young_Knight, Determined_Man, Lovely_Girl, Decent_Boy, Imposing_Manner, Elegant_Man, Abbess, Sweet_Girl_2, Exube
- `speed`: number No 1 0.50 ~ 2.00 Speech speed. Range: 0.5-2.0, where 1.0 is normal speed.
- `volume`: number No 1 0.10 ~ 10.00 Speech volume. Range: 0.1-10.0, where 1.0 is normal volume.
- `pitch`: number No - -12 ~ 12 Speech pitch. Range: -12 to 12, where 0 is normal pitch.
- `emotion`: string No happy happy, sad, angry, fearful, disgusted, surprised, neutral The emotion of the generated speech.
- `english_normalization`: boolean No false - This parameter supports English text normalization, which improves performance in number-reading scenarios.
- `sample_rate`: integer No - 8000, 16000, 22050, 24000, 32000, 44100 Sample rate of generated sound.
- `bitrate`: integer No - 32000, 64000, 128000, 256000 Bitrate of generated sound.
- `channel`: string No - 1, 2 The number of channels of the generated audio. 1: mono, 2: stereo.
- `format`: string No - mp3, wav, pcm, flac Format of generated sound.
- `language_boost`: string No - Chinese, Chinese,Yue, English, Arabic, Russian, Spanish, French, Portuguese, German, Turkish, Dutch, Ukrainian, Vietnamese, Indonesian, Japanese, Italian, Korean, Thai, Polish, Romanian, Greek, Czech, Finnish, Hindi, Bulgarian, 
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Nvidia Nemotron 3 Nano Omni Audio

- **Model ID:** `nvidia/nemotron-3-nano-omni/audio`
- **Operation:** `music_generation`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/nvidia/nemotron-3-nano-omni/audio`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/nvidia/nvidia-nemotron-3-nano-omni-audio

**Request Parameters**

- `prompt`: string Yes - Text prompt to send to the model. English only.
- `audio_url`: string Yes - - URL of the audio to reason about.
- `system_prompt`: string No - - Optional system prompt to steer the model.
- `reasoning_mode`: string No no_think no_think, think Whether the model should emit an explicit reasoning trace.
- `max_tokens`: integer No 1024 - Maximum number of tokens to generate.
- `temperature`: number No 0.7 - Sampling temperature. Lower values are more deterministic.
- `top_p`: number No 0.95 0 ~ 1 Nucleus sampling probability mass.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Ace Step

- **Model ID:** `wavespeed-ai/ace-step`
- **Operation:** `music_generation`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/ace-step`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/ace-step

**Request Parameters**

- `tags`: string Yes - - Comma-separated list of genre tags to control the style of the generated audio.
- `lyrics`: string No - - Vocal content for the track. Use [inst] or [instrumental] for no vocals.
- `duration`: number No 60 5 ~ 240 Audio length in seconds.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed for reproducibility.

### Ace Step 1.5

- **Model ID:** `wavespeed-ai/ace-step-1.5`
- **Operation:** `music_generation`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/ace-step-1.5`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/ace-step-1.5

**Request Parameters**

- `tags`: string Yes - - Comma-separated list of genre tags to control the style of the generated audio.
- `lyrics`: string Yes - - Vocal content for the track. Use [inst] or [instrumental] for no vocals.
- `duration`: number No 60 5 ~ 240 Audio length in seconds.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed for reproducibility.

### Ace Step Audio Inpaint

- **Model ID:** `wavespeed-ai/ace-step/audio-inpaint`
- **Operation:** `audio_edit`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/ace-step/audio-inpaint`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/ace-step-audio-inpaint

**Request Parameters**

- `audio`: string Yes - - Audio file to transcribe. Provide an HTTPS URL or upload a file (MP3, WAV, FLAC up to 60 minutes).
- `tags`: string Yes - - Comma-separated list of genre tags to control the style.
- `start_time_relative_to`: string No start start, end Reference point for start time.
- `start_time`: number No - 0 ~ 240 Start time in seconds.
- `end_time_relative_to`: string No start start, end Reference point for end time.
- `end_time`: number No 30 0 ~ 240 End time in seconds.
- `lyrics`: string No - - Lyrics to be sung in the audio. Use [inst] or [instrumental] for no vocals.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed for reproducibility.

### Ace Step Audio Outpaint

- **Model ID:** `wavespeed-ai/ace-step/audio-outpaint`
- **Operation:** `audio_edit`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/ace-step/audio-outpaint`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/ace-step-audio-outpaint

**Request Parameters**

- `audio`: string Yes - - Audio file to transcribe. Provide an HTTPS URL or upload a file (MP3, WAV, FLAC up to 60 minutes).
- `tags`: string Yes - - Comma-separated list of genre tags to control the style.
- `extend_before_duration`: number No - 0 ~ 240 Duration to extend from the start in seconds.
- `extend_after_duration`: number No 30 0 ~ 240 Duration to extend from the end in seconds.
- `lyrics`: string No - - Vocal content for generation. Use [inst] or [instrumental] for no vocals.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed for reproducibility.

### Ace Step Audio To Audio

- **Model ID:** `wavespeed-ai/ace-step/audio-to-audio`
- **Operation:** `audio_edit`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/ace-step/audio-to-audio`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/ace-step-audio-to-audio

**Request Parameters**

- `audio`: string Yes - - Audio file to transcribe. Provide an HTTPS URL or upload a file (MP3, WAV, FLAC up to 60 minutes).
- `original_tags`: string Yes - - Original genre tags of the audio file.
- `tags`: string Yes - - Comma-separated list of genre tags to control the style.
- `edit_mode`: string No remix lyrics, remix Edit mode: lyrics or remix.
- `original_lyrics`: string No - - Original lyrics of the audio.
- `lyrics`: string No - - New lyrics for generation.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed for reproducibility.

### Ace Step Prompt To Audio

- **Model ID:** `wavespeed-ai/ace-step/prompt-to-audio`
- **Operation:** `music_generation`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/ace-step/prompt-to-audio`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/ace-step-prompt-to-audio

**Request Parameters**

- `prompt`: string Yes - Prompt to control the style of the generated audio. This will be used to generate tags and lyrics.
- `instrumental`: boolean No false - Whether to generate an instrumental version.
- `duration`: number No 60 5 ~ 240 Audio length in seconds.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed for reproducibility.

### Audio Converter

- **Model ID:** `wavespeed-ai/audio-converter`
- **Operation:** `music_generation`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/audio-converter`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/audio-converter

**Request Parameters**

- `audio`: string Yes - - The URL of the input audio.
- `output_format`: string Yes - mp3, wav, aac, flac, ogg, m4a, wma The target format to convert the audio to (mp3, wav, aac, flac, ogg, m4a, wma).

### Audio Vocal Isolator

- **Model ID:** `wavespeed-ai/audio-vocal-isolator`
- **Operation:** `music_generation`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/audio-vocal-isolator`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/audio-vocal-isolator

**Request Parameters**

- `audio`: string Yes - - The URL of the input audio file.

### Heartmula Generate Music

- **Model ID:** `wavespeed-ai/heartmula/generate-music`
- **Operation:** `music_generation`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/heartmula/generate-music`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/heartmula-generate-music

**Request Parameters**

- `lyrics`: string Yes - - Song lyrics with structure tags. Each paragraph represents a segment starting with a structure tag (e.g., [verse], [chorus], [bridge]) and ending with a blank line. Each line is a sentence without punctuation.
- `tags`: string No - - Musical style tags (Optional). Describe the genre, mood, instruments, tempo, etc. For example: 'pop, upbeat, electronic, female vocals'.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Heartmula Transcribe Lyrics

- **Model ID:** `wavespeed-ai/heartmula/transcribe-lyrics`
- **Operation:** `music_generation`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/heartmula/transcribe-lyrics`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/heartmula-transcribe-lyrics

**Request Parameters**

- `audio`: string Yes - - URL to the audio file to transcribe lyrics from.

### Mmaudio V2

- **Model ID:** `wavespeed-ai/mmaudio-v2`
- **Operation:** `music_generation`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/mmaudio-v2`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/mmaudio-v2

**Request Parameters**

- `video`: string Yes - The URL of the video to generate the audio for.
- `prompt`: string Yes - The positive prompt for the generation.
- `negative_prompt`: string No - The negative prompt for the generation.
- `num_inference_steps`: integer No 25 4 ~ 50 The number of inference steps to perform.
- `duration`: integer No 8 1 ~ 30 The duration of the generated media in seconds.
- `guidance_scale`: number No 4.5 0 ~ 20 The guidance scale to use for the generation.
- `mask_away_clip`: boolean No false - Whether to mask away the clip.

### Music Video Generator

- **Model ID:** `wavespeed-ai/music-video-generator`
- **Operation:** `music_generation`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/music-video-generator`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/music-video-generator

**Request Parameters**

- `audio`: string Yes - - The audio/music file URL for generating the music video.
- `images`: array No [] - List of reference image URLs (1-3 images). The person in the images will appear throughout the video.
- `prompt`: string No - Style and scene description for the music video (e.g. "A woman sings in a forest while playing a guitar").
- `aspect_ratio`: string No - 16:9, 9:16 Aspect ratio of the output video. If not specified, auto-detected from input images.
- `resolution`: string No 480p 480p, 720p The resolution of the output video.

### Omnivoice Text To Speech

- **Model ID:** `wavespeed-ai/omnivoice/text-to-speech`
- **Operation:** `text_to_speech`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/omnivoice/text-to-speech`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/omnivoice-text-to-speech

**Request Parameters**

- `text`: string Yes - - The text content to convert into speech. Supports 600+ languages.
- `voice_description`: string No - - Comma-separated voice attributes. If omitted, a random voice is used. Valid English attributes: female, male, child, teenager, young adult, middle-aged, elderly, low pitch, moderate pitch, high pitch, very low pitch, very high
- `speed`: number No 1 0 ~ 5 Playback speed factor. 1.0 = normal speed. Values > 1.0 are faster, < 1.0 are slower.

### Qwen3 Tts Text To Speech

- **Model ID:** `wavespeed-ai/qwen3-tts/text-to-speech`
- **Operation:** `text_to_speech`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/qwen3-tts/text-to-speech`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/qwen3-tts-text-to-speech

**Request Parameters**

- `text`: string Yes - - The text content to convert into speech
- `language`: string Yes auto auto, Chinese, English, German, Italian, Portuguese, Spanish, Japanese, Korean, French, Russian Language of the speech output (use 'auto' for automatic detection)
- `voice`: string Yes Vivian Vivian, Serena, Ono_Anna, Sohee, Uncle_Fu, Dylan, Eric, Ryan, Aiden Voice character to use for speech synthesis
- `style_instruction`: string No - - Optional instruction to control the speaking style, tone, or emotion

### Song Generation

- **Model ID:** `wavespeed-ai/song-generation`
- **Operation:** `music_generation`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/song-generation`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/song-generation

**Request Parameters**

- `lyric`: string Yes - - Each paragraph represents a segment starting with a structure tag and ending with a blank line, each line is a sentence without punctuation, segments [intro], [inst], [outro] should not contain lyrics, while [verse], [chorus]
- `description`: string No - - Song Description (Optional). Describe the gender, timbre, genre, emotion, instrument and bpm of the song. Only English is supported currently.
- `prompt_audio`: string No - - Prompt Audio (Optional). Provide a URL to an audio file that serves as a prompt for the genre of the song generation.
- `genre`: string No Auto Pop, R&B, Dance, Jazz, Folk, Rock, Chinese Style, Chinese Tradition, Metal, Reggae, Chinese Opera, Auto Genre Select (Optional). Choose a genre for the song.
- `guidance_scale`: number No 1.5 0.1 ~ 3.0 The guidance scale to use for the generation.
- `temperature`: number No 0.9 0.1 ~ 2.0 The temperature to use for the generation. A higher value means more randomness in the output.
- `top_k`: integer No 50 1 ~ 100 The top-k value to use for the generation. This controls the number of highest probability vocabulary tokens to keep for top-k-filtering.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Vibevoice

- **Model ID:** `wavespeed-ai/vibevoice`
- **Operation:** `music_generation`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/vibevoice`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/vibevoice

**Request Parameters**

- `text`: string Yes - - Text to translate
- `speaker`: string No Frank Frank, Wayne, Carter, Emma, Grace, Mike Voice to use for speaking.

### Video Outpainter

- **Model ID:** `wavespeed-ai/video-outpainter`
- **Operation:** `audio_edit`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/video-outpainter`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/video-outpainter

**Request Parameters**

- `video`: string Yes - The video for generating the output.
- `prompt`: string No - The positive prompt for the generation.
- `aspect_ratio`: string No auto auto, 1:1, 4:3, 3:4, 16:9, 9:16, 3:2, 2:3, 21:9, 9:21 The aspect ratio of the generated media.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Void Video Inpainting Mask

- **Model ID:** `wavespeed-ai/void-video-inpainting/mask`
- **Operation:** `audio_edit`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/void-video-inpainting/mask`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/void-video-inpainting-mask

**Request Parameters**

- `prompt`: string Yes - Text description of the desired background after object removal.
- `video`: string Yes - URL of the input video containing the object to remove.
- `mask_video`: string No - - URL of a mask video for the removal target. For best results this should be a VOID-style quadmask video with 4 grayscale values: 0=object to remove, 63=overlap, 127=affected region, 255=background to keep. A simple binary mask
- `mask_prompt`: string No - - Text description of what should be masked in the input video, such as the object or person to remove. Used to generate a temporary mask video with SAM-3 when `quad_mask_video_url` is not provided.
- `enable_pass2_refinement`: boolean No false - Run VOID Pass 2 warped-noise refinement after Pass 1. This is slower but can improve temporal consistency on longer clips.
- `negative_prompt`: string No - Negative prompt to guide generation away from undesired outputs.
- `num_inference_steps`: integer No 30 1 ~ 50 Number of denoising steps. Higher values improve quality but increase latency.
- `guidance_scale`: number No 1 0 ~ 20 Classifier-free guidance scale.
- `strength`: number No 1 0 ~ 1 Denoising strength. 1.0 means full denoising.
- `num_frames`: integer No 85 1 ~ 197 Temporal window size for inference. The backend snaps this to the nearest CogVideoX-safe value that works with temporal compression and patching. Valid outputs are 69, 77, 85, ..., 197.
- `seed`: integer No - -1 ~ 2147483647 Random seed for reproducibility.

### Z Image Turbo Inpaint

- **Model ID:** `wavespeed-ai/z-image/turbo-inpaint`
- **Operation:** `audio_edit`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/z-image/turbo-inpaint`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/z-image-turbo-inpaint

**Request Parameters**

- `image`: string Yes - URL of the input image to be inpainted.
- `prompt`: string Yes - The text description for the inpainting task.
- `mask_image`: string Yes - URL of the mask image. White areas will be inpainted, black areas will be preserved.
- `size`: string No - 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).


## Category: image

### Akool Image Face Swap

- **Model ID:** `akool/image-face-swap`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/akool/image-face-swap`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/akool/akool-image-face-swap

**Request Parameters**

- `image`: string Yes - Image URL to be swapped
- `source_image`: array Yes - 1 ~ 5 items Source face image URL to be swapped into the video
- `target_image`: array Yes - 1 ~ 5 items Target face image URL that will be replaced in the video
- `face_enhance`: boolean No false - Whether to enhance face quality after swapping
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Alibaba Qwen Image Translate

- **Model ID:** `alibaba/qwen-image/translate`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/alibaba/qwen-image/translate`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/alibaba/alibaba-qwen-image-translate

**Request Parameters**

- `image`: string Yes - The image to process for translation
- `source_lang`: string No auto auto, en, zh, ja, ko, fr, de, es, ru, ar Source language code (auto for auto-detection)
- `target_lang`: string Yes zh en, zh, ja, ko, fr, de, es, ru, ar Target language code for translation
- `domain_hint`: string No - - If you want the translation style to be more in line with the characteristics of a certain field, you can use English to describe the usage scenario, translation style and other field requirements. In order to ensure the trans
- `sensitives`: array No [] - Array of sensitive words to filter
- `terminologies`: array No [] - Array of terminoogies to use for translation
- `skip_image_segment`: boolean No false - Whether to skip image segmentation

### Alibaba Wan 2.5 Image Edit

- **Model ID:** `alibaba/wan-2.5/image-edit`
- **Operation:** `edit_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/alibaba/wan-2.5/image-edit`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/alibaba/alibaba-wan-2.5-image-edit

**Request Parameters**

- `images`: array Yes [] 1 ~ 2 items List of URLs of input images for editing. The maximum number of images is 2.
- `prompt`: string Yes - The positive prompt for the generation.
- `negative_prompt`: string No - The negative prompt for the generation.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Alibaba Wan 2.5 Text To Image

- **Model ID:** `alibaba/wan-2.5/text-to-image`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/alibaba/wan-2.5/text-to-image`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/alibaba/alibaba-wan-2.5-text-to-image

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `negative_prompt`: string No - The negative prompt for the generation.
- `size`: string No 1024*1024 768 ~ 1440 per dimension The size of the generated image in pixels (width*height).
- `enable_prompt_expansion`: boolean No false - If set to true, the prompt optimizer will be enabled.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Alibaba Wan 2.6 Image Edit

- **Model ID:** `alibaba/wan-2.6/image-edit`
- **Operation:** `edit_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/alibaba/wan-2.6/image-edit`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/alibaba/alibaba-wan-2.6-image-edit

**Request Parameters**

- `images`: array Yes [] 1 ~ 3 items List of URLs of input images for editing. The maximum number of images is 3.
- `prompt`: string Yes - The positive prompt for the generation.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `enable_prompt_expansion`: boolean No false - If set to true, the prompt optimizer will be enabled.

### Alibaba Wan 2.6 Text To Image

- **Model ID:** `alibaba/wan-2.6/text-to-image`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/alibaba/wan-2.6/text-to-image`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/alibaba/alibaba-wan-2.6-text-to-image

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 1024*1024 768 ~ 1440 per dimension The size of the generated image in pixels (width*height).
- `enable_prompt_expansion`: boolean No false - If set to true, the prompt optimizer will be enabled.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Alibaba Wan 2.7 Image Edit

- **Model ID:** `alibaba/wan-2.7/image-edit`
- **Operation:** `edit_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/alibaba/wan-2.7/image-edit`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/alibaba/alibaba-wan-2.7-image-edit

**Request Parameters**

- `images`: array Yes [] 1 ~ 9 items List of URLs of input images for editing (1-9 images).
- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No - 512 ~ 4096 per dimension The size of the generated image in pixels (width*height). Range: 512-4096 per dimension. Total pixels must be between 768*768 and 2048*2048. Aspect ratio must be between 1:8 and 8:1.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Alibaba Wan 2.7 Image Edit Pro

- **Model ID:** `alibaba/wan-2.7/image-edit-pro`
- **Operation:** `edit_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/alibaba/wan-2.7/image-edit-pro`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/alibaba/alibaba-wan-2.7-image-edit-pro

**Request Parameters**

- `images`: array Yes [] 1 ~ 9 items List of URLs of input images for editing (1-9 images).
- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No - 512 ~ 4096 per dimension The size of the generated image in pixels (width*height). Range: 512-4096 per dimension. Total pixels must be between 768*768 and 2048*2048. Aspect ratio must be between 1:8 and 8:1.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Alibaba Wan 2.7 Text To Image

- **Model ID:** `alibaba/wan-2.7/text-to-image`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/alibaba/wan-2.7/text-to-image`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/alibaba/alibaba-wan-2.7-text-to-image

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 1024*1024 512 ~ 4096 per dimension The size of the generated image in pixels (width*height). Range: 512-4096 per dimension. Total pixels must be between 768*768 and 2048*2048. Aspect ratio must be between 1:8 and 8:1.
- `thinking_mode`: boolean No true - Enable thinking mode for enhanced reasoning and better image quality. Increases generation time.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Alibaba Wan 2.7 Text To Image Pro

- **Model ID:** `alibaba/wan-2.7/text-to-image-pro`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/alibaba/wan-2.7/text-to-image-pro`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/alibaba/alibaba-wan-2.7-text-to-image-pro

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 1024*1024 512 ~ 8192 per dimension The size of the generated image in pixels (width*height). Range: 512-8192 per dimension. Total pixels must be between 768*768 and 4096*4096. Aspect ratio must be between 1:8 and 8:1.
- `thinking_mode`: boolean No true - Enable thinking mode for enhanced reasoning and better image quality. Increases generation time.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Bria Embed Product

- **Model ID:** `bria/embed-product`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bria/embed-product`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bria/bria-embed-product

**Request Parameters**

- `image`: string Yes - TURL of the image.
- `products`: array Yes [{}] - This is a controlnet that controls the maximum size of the generated model.
- `seed`: integer No - -1 ~ 2147483647 Seed for random number generator. Set to -1 to use a random seed.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Bria Eraser

- **Model ID:** `bria/eraser`
- **Operation:** `edit_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bria/eraser`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bria/bria-eraser

**Request Parameters**

- `image`: string Yes - The URL of the image to erase.
- `mask_image`: string Yes - The URL of the mask image to generate an image from.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Bria Expand

- **Model ID:** `bria/expand`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bria/expand`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bria/bria-expand

**Request Parameters**

- `image`: string Yes - The URL of the image to erase.
- `aspect_ratio`: string Yes 1:1 1:1, 2:3, 3:2, 3:4, 4:3, 4:5, 5:4, 9:16, 16:9 Aspect ratio for expansion.Input Image Area: Ensure that the ratio of the input image foreground or main subject to the canvas area is greater than 15% to achieve optimal results.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Bria Fibo Colorize

- **Model ID:** `bria/fibo/colorize`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bria/fibo/colorize`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bria/bria-fibo-colorize

**Request Parameters**

- `image`: string Yes - The source image to colorize.
- `style`: string Yes contemporary color contemporary color, vivid color, black and white colors, sepia vintage The colorization style to apply.

### Bria Fibo Image Blend

- **Model ID:** `bria/fibo/image-blend`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bria/fibo/image-blend`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bria/bria-fibo-image-blend

**Request Parameters**

- `image`: string Yes - The source image to be blended.
- `prompt`: string Yes - Free-text command describing the blend operation.

### Bria Fibo Relight

- **Model ID:** `bria/fibo/relight`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bria/fibo/relight`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bria/bria-fibo-relight

**Request Parameters**

- `image`: string Yes - The source image to be relighted.
- `light_type`: string Yes midday midday, blue hour light, low-angle sunlight, sunrise light, spotlight on subject, overcast light, soft overcast daylight lighting, cloud-filtered lighting, fog-diffused lighting, moonlight lighting, starlight nighttime, so
- `light_direction`: string No front front, side, bottom, top-down The direction of the light source.

### Bria Fibo Reseason

- **Model ID:** `bria/fibo/reseason`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bria/fibo/reseason`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bria/bria-fibo-reseason

**Request Parameters**

- `image`: string Yes - The source image to change season.
- `season`: string Yes spring spring, summer, autumn, winter The desired season to apply.

### Bria Fibo Restore

- **Model ID:** `bria/fibo/restore`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bria/fibo/restore`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bria/bria-fibo-restore

**Request Parameters**

- `image`: string Yes - The source image to be restored.

### Bria Generate Background

- **Model ID:** `bria/generate-background`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bria/generate-background`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bria/bria-generate-background

**Request Parameters**

- `image`: string Yes - The URL of the image to erase.
- `prompt`: string Yes - Text description of the new scene or background
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Bria Fibo Edit

- **Model ID:** `bria/image-3.2`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bria/image-3.2`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bria/bria-fibo-edit

**Request Parameters**

- `images`: array Yes [] 1 ~ 1 items Required. The source image to be edited. Publicly available URL or Base64-encoded. Accepted formats: JPEG, JPG, PNG, WEBP. Must contain exactly one item.
- `prompt`: string No - Text prompt for image generation
- `mask_image`: string No - The URL of the mask image to generate an image from.
- `negative_prompt`: string No - The negative prompt for the generation.
- `structured_prompt`: string No - - Structured prompt (JSON string). Use a structured_prompt from a previous generation's response or the /v2/structured_prompt/generate endpoint for precise refinement.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Bria Text To Image 3.2

- **Model ID:** `bria/image-3.2`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bria/image-3.2`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bria/bria-text-to-image-3.2

**Request Parameters**

- `prompt`: string Yes - Text prompt for image generation
- `aspect_ratio`: string No 1:1 1:1, 2:3, 3:2, 3:4, 4:3, 4:5, 5:4, 9:16, 16:9 The aspect ratio of the generated media.
- `negative_prompt`: string No - The negative prompt for the generation.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Bria Fibo

- **Model ID:** `bria/image-3.2`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bria/image-3.2`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bria/bria-fibo

**Request Parameters**

- `prompt`: string Yes - Text prompt for image generation
- `aspect_ratio`: string No 1:1 1:1, 2:3, 3:2, 3:4, 4:3, 4:5, 5:4, 9:16, 16:9 The aspect ratio of the generated media.
- `negative_prompt`: string No - The negative prompt for the generation.
- `structured_prompt`: string No - - Structured prompt (JSON string). Use a structured_prompt from a previous generation's response or the /v2/structured_prompt/generate endpoint for precise refinement.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Bria Increase Resolution

- **Model ID:** `bria/increase-resolution`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bria/increase-resolution`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bria/bria-increase-resolution

**Request Parameters**

- `image`: string Yes - The URL of the image to erase.
- `desired_increase`: integer No 2 2, 4 Resolution multiplier. Possible values are 2 or 4. Maximum total area is 8192x8192 pixels
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Bytedance Dreamactor V2

- **Model ID:** `bytedance/dreamactor-v2`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bytedance/dreamactor-v2`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bytedance/bytedance-dreamactor-v2

**Request Parameters**

- `image`: string Yes - Reference image (JPEG/PNG). Max 4.7MB. Resolution: 480x480 to 1920x1080.
- `video`: string Yes - Driving video (MP4/MOV/WebM). Max 30 seconds. Resolution: 200x200 to 2048x1440.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result before returning. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string. This property is only available through the API.

### Bytedance Dreamina V3.0 Edit

- **Model ID:** `bytedance/dreamina-v3.0/edit`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bytedance/dreamina-v3.0/edit`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bytedance/bytedance-dreamina-v3.0-edit

**Request Parameters**

- `image`: string Yes - The image to edit, can be a URL or base64 encoded image.
- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 1328*1328 512 ~ 2048 per dimension The size of the generated media in pixels (width*height).
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Bytedance Dreamina V3.0 Text To Image

- **Model ID:** `bytedance/dreamina-v3.0/text-to-image`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bytedance/dreamina-v3.0/text-to-image`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bytedance/bytedance-dreamina-v3.0-text-to-image

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 1328*1328 512 ~ 2048 per dimension The size of the generated media in pixels (width*height).
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `enable_prompt_expansion`: boolean No true - If set to true, the function will wait for the image to be generated and uploaded before returning the response. It allows you to get the image directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Bytedance Dreamina V3.1 Text To Image

- **Model ID:** `bytedance/dreamina-v3.1/text-to-image`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bytedance/dreamina-v3.1/text-to-image`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bytedance/bytedance-dreamina-v3.1-text-to-image

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 1328*1328 512 ~ 2048 per dimension The size of the generated media in pixels (width*height).
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `enable_prompt_expansion`: boolean No true - If set to true, the function will wait for the image to be generated and uploaded before returning the response. It allows you to get the image directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Bytedance Latentsync

- **Model ID:** `bytedance/latentsync`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bytedance/latentsync`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bytedance/bytedance-latentsync

**Request Parameters**

- `audio`: string Yes - - The audio for generating the output.
- `video`: string Yes - The video for generating the output.

### Bytedance Seededit V3

- **Model ID:** `bytedance/seededit-v3`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bytedance/seededit-v3`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bytedance/bytedance-seededit-v3

**Request Parameters**

- `image`: string Yes - The image to edit, can be a URL or base64 encoded image.
- `prompt`: string Yes - The positive prompt for the generation.
- `guidance_scale`: number No 0.5 0.0 ~ 1.0 The guidance scale to use for the generation.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Bytedance Seedream V3

- **Model ID:** `bytedance/seedream-v3`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bytedance/seedream-v3`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bytedance/bytedance-seedream-v3

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 1024*1024 512 ~ 2048 per dimension The size of the generated media in pixels (width*height).
- `guidance_scale`: number No 2.5 0.0 ~ 20.0 The guidance scale to use for the generation.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Bytedance Seedream V3.1

- **Model ID:** `bytedance/seedream-v3.1`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bytedance/seedream-v3.1`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bytedance/bytedance-seedream-v3.1

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 1024*1024 512 ~ 2048 per dimension The size of the generated media in pixels (width*height).
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `enable_prompt_expansion`: boolean No true - If set to true, the function will wait for the image to be generated and uploaded before returning the response. It allows you to get the image directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Bytedance Seedream V4

- **Model ID:** `bytedance/seedream-v4`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bytedance/seedream-v4`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bytedance/bytedance-seedream-v4

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 2048*2048 512 ~ 8192 per dimension The size of the generated media, supporting up to 4K resolution for images. If you need to match the size of an existing image, you must explicitly specify the dimensions, as automatic resizing t
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Bytedance Seedream V4.5

- **Model ID:** `bytedance/seedream-v4.5`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bytedance/seedream-v4.5`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bytedance/bytedance-seedream-v4.5

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 2048*2048 512 ~ 8192 per dimension Specify the width and height pixel values of the generated image.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Bytedance Seedream V4.5 Edit

- **Model ID:** `bytedance/seedream-v4.5/edit`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bytedance/seedream-v4.5/edit`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bytedance/bytedance-seedream-v4.5-edit

**Request Parameters**

- `images`: array Yes [] 1 ~ 10 items The images to edit. A maximum of 10 reference images can be uploaded.
- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No - 512 ~ 8192 per dimension Specify the width and height pixel values of the generated image.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Bytedance Seedream V4.5 Edit Sequential

- **Model ID:** `bytedance/seedream-v4.5/edit-sequential`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bytedance/seedream-v4.5/edit-sequential`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bytedance/bytedance-seedream-v4.5-edit-sequential

**Request Parameters**

- `images`: array Yes [] 1 ~ 10 items The images to edit. A maximum of 10 reference images can be uploaded.
- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No - 512 ~ 8192 per dimension Specify the width and height pixel values of the generated image.
- `max_images`: integer No 1 1 ~ 15 The maximum number of images that can be generated (up to 15). This value must align with the number of images specified in the prompt above.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Bytedance Seedream V4.5 Sequential

- **Model ID:** `bytedance/seedream-v4.5/sequential`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bytedance/seedream-v4.5/sequential`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bytedance/bytedance-seedream-v4.5-sequential

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 2048*2048 512 ~ 8192 per dimension Specify the width and height pixel values of the generated image.
- `max_images`: integer No 1 1 ~ 15 The maximum number of images that can be generated (up to 15). This value must align with the number of images specified in the prompt above.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Bytedance Seedream V4 Edit

- **Model ID:** `bytedance/seedream-v4/edit`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bytedance/seedream-v4/edit`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bytedance/bytedance-seedream-v4-edit

**Request Parameters**

- `images`: array Yes [] 1 ~ 10 items The images to edit. A maximum of 10 reference images can be uploaded.
- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No - 512 ~ 8192 per dimension The size of the generated media, supporting up to 4K resolution for images. If you need to match the size of an existing image, you must explicitly specify the dimensions, as automatic resizing to match 
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Bytedance Seedream V4 Edit Sequential

- **Model ID:** `bytedance/seedream-v4/edit-sequential`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bytedance/seedream-v4/edit-sequential`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bytedance/bytedance-seedream-v4-edit-sequential

**Request Parameters**

- `images`: array Yes [] 1 ~ 10 items The images to edit. A maximum of 10 reference images can be uploaded.
- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No - 512 ~ 8192 per dimension The size of the generated media in pixels (width*height).
- `max_images`: integer No 1 1 ~ 15 The maximum number of images that can be generated (up to 15). This value must align with the number of images specified in the prompt above.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Bytedance Seedream V4 Sequential

- **Model ID:** `bytedance/seedream-v4/sequential`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bytedance/seedream-v4/sequential`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bytedance/bytedance-seedream-v4-sequential

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 2048*2048 512 ~ 8192 per dimension The size of the generated media, supporting up to 4K resolution for images. If you need to match the size of an existing image, you must explicitly specify the dimensions, as automatic resizing t
- `max_images`: integer No 1 1 ~ 15 The maximum number of images that can be generated (up to 15). This value must align with the number of images specified in the prompt above.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Bytedance Seedream V5.0 Lite

- **Model ID:** `bytedance/seedream-v5.0-lite`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bytedance/seedream-v5.0-lite`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bytedance/bytedance-seedream-v5.0-lite

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 2048*2048 1440 ~ 8192 per dimension Specify the width and height pixel values of the generated image.Total pixel value range: [2560*1440, 4096*4096]
- `output_format`: string No - jpeg, png The format of the output image.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Bytedance Seedream V5.0 Lite Edit

- **Model ID:** `bytedance/seedream-v5.0-lite/edit`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bytedance/seedream-v5.0-lite/edit`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bytedance/bytedance-seedream-v5.0-lite-edit

**Request Parameters**

- `images`: array Yes [] 1 ~ 10 items The images to edit. A maximum of 10 reference images can be uploaded.
- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No - 512 ~ 8192 per dimension Specify the width and height pixel values of the generated image.
- `output_format`: string No - jpeg, png The format of the output image.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Bytedance Seedream V5.0 Lite Edit Sequential

- **Model ID:** `bytedance/seedream-v5.0-lite/edit-sequential`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bytedance/seedream-v5.0-lite/edit-sequential`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bytedance/bytedance-seedream-v5.0-lite-edit-sequential

**Request Parameters**

- `images`: array Yes [] 1 ~ 10 items The images to edit. A maximum of 10 reference images can be uploaded.
- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No - 512 ~ 8192 per dimension Specify the width and height pixel values of the generated image.
- `max_images`: integer No 1 1 ~ 15 The maximum number of images that can be generated (up to 15). This value must align with the number of images specified in the prompt above.
- `output_format`: string No - jpeg, png The format of the output image.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Bytedance Seedream V5.0 Lite Sequential

- **Model ID:** `bytedance/seedream-v5.0-lite/sequential`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bytedance/seedream-v5.0-lite/sequential`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bytedance/bytedance-seedream-v5.0-lite-sequential

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 2048*2048 1440 ~ 8192 per dimension Specify the width and height pixel values of the generated image.Total pixel value range: [2560*1440, 4096*4096]
- `max_images`: integer No 1 1 ~ 15 The maximum number of images that can be generated (up to 15). This value must align with the number of images specified in the prompt above.
- `output_format`: string No - jpeg, png The format of the output image.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Bytedance Uso

- **Model ID:** `bytedance/uso`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bytedance/uso`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bytedance/bytedance-uso

**Request Parameters**

- `prompt`: string No - The positive prompt for the generation.
- `reference_images`: array Yes - 1 ~ 4 items A list of images to use as style references. At least 1 image is required. max 4 images.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `sync_model`: boolean No false - Sync model to the latest version.

### Bytedance Waver 1.0

- **Model ID:** `bytedance/waver-1.0`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bytedance/waver-1.0`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bytedance/bytedance-waver-1.0

**Request Parameters**

- `image`: string Yes - The image for generating the output.
- `prompt`: string Yes - The positive prompt for the generation.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Decart Lucy Edit Dev

- **Model ID:** `decart/lucy-edit-dev`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/decart/lucy-edit-dev`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/decart/decart-lucy-edit-dev

**Request Parameters**

- `video`: string Yes - The video to translate.
- `prompt`: string Yes - The prompt to edit the video.

### Decart Lucy Edit Pro

- **Model ID:** `decart/lucy-edit-pro`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/decart/lucy-edit-pro`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/decart/decart-lucy-edit-pro

**Request Parameters**

- `video`: string Yes - The video to translate.
- `prompt`: string Yes - The prompt to edit the video.
- `resolution`: string No 720p 720p, 480p The resolution of the video (720p/480p).

### Decart Lucy Restyle

- **Model ID:** `decart/lucy-restyle`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/decart/lucy-restyle`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/decart/decart-lucy-restyle

**Request Parameters**

- `video`: string Yes - The video to translate.
- `prompt`: string Yes - The prompt to edit the video.

### Elevenlabs Dubbing

- **Model ID:** `elevenlabs/dubbing`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/elevenlabs/dubbing`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/elevenlabs/elevenlabs-dubbing

**Request Parameters**

- `video`: string No - URL of the video file to dub. Either video or audio must be provided. If both are provided, video takes priority.
- `audio`: string No - - URL of the audio file to dub. Either video or audio must be provided.
- `target_lang`: string Yes - English, Spanish, French, German, Italian, Portuguese, Chinese, Japanese, Korean, Russian, Arabic, Hindi, Dutch, Polish, Turkish, Vietnamese, Thai, Indonesian Target language for dubbing
- `source_lang`: string No Auto Auto, English, Spanish, French, German, Italian, Portuguese, Chinese, Japanese, Korean, Russian, Arabic, Hindi, Dutch, Polish, Turkish, Vietnamese, Thai, Indonesian Source language. Select 'Auto' for automatic detection.

### Elevenlabs Multilingual V1

- **Model ID:** `elevenlabs/multilingual-v1`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/elevenlabs/multilingual-v1`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/elevenlabs/elevenlabs-multilingual-v1

**Request Parameters**

- `text`: string Yes - - Text to convert to speech. Every character is 1 token. Maximum 10000 characters. Use <#x#> between words to control pause duration (0.01-99.99s).
- `voice_id`: string Yes Alice Aria, Roger, Sarah, Laura, Charlie, George, Callum, River, Liam, Charlotte, Alice, Matilda, Will, Jessica, Eric, Chris, Brian, Daniel, Lily, Bill The voice to use for speech generation
- `similarity`: number No 1 0.00 ~ 1.00 High enhancement boosts overall voice clarity and target speaker similarity. Very high values can cause artifacts, so adjusting this setting to find the optimal value is encouraged.
- `stability`: number No 0.5 0.00 ~ 1.00 Voice stability (0-1) Default value: 0.5
- `use_speaker_boost`: boolean No true - This parameter supports English text normalization, which improves performance in number-reading scenarios.

### Elevenlabs Multilingual V2

- **Model ID:** `elevenlabs/multilingual-v2`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/elevenlabs/multilingual-v2`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/elevenlabs/elevenlabs-multilingual-v2

**Request Parameters**

- `text`: string Yes - - Text to convert to speech. Every character is 1 token. Maximum 10000 characters. Use <#x#> between words to control pause duration (0.01-99.99s).
- `voice_id`: string Yes Alice Aria, Roger, Sarah, Laura, Charlie, George, Callum, River, Liam, Charlotte, Alice, Matilda, Will, Jessica, Eric, Chris, Brian, Daniel, Lily, Bill The voice to use for speech generation
- `similarity`: number No 1 0.00 ~ 1.00 High enhancement boosts overall voice clarity and target speaker similarity. Very high values can cause artifacts, so adjusting this setting to find the optimal value is encouraged.
- `stability`: number No 0.5 0.00 ~ 1.00 Voice stability (0-1) Default value: 0.5
- `use_speaker_boost`: boolean No true - This parameter supports English text normalization, which improves performance in number-reading scenarios.

### Google Gemini 2.5 Flash Image Preview Edit

- **Model ID:** `google/gemini-2.5-flash-image-preview/edit`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/google/gemini-2.5-flash-image-preview/edit`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/google/google-gemini-2.5-flash-image-preview-edit

**Request Parameters**

- `images`: array Yes [] 1 ~ 10 items List of URLs of input images for editing. Up to 10 images can be provided.
- `prompt`: string Yes - The positive prompt for the generation.
- `aspect_ratio`: string No - 1:1, 3:2, 2:3, 3:4, 4:3, 4:5, 5:4, 9:16, 16:9, 21:9 The aspect ratio of the generated media.
- `output_format`: string No png png, jpeg The format of the output image.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Google Gemini 2.5 Flash Image Preview Text To Image

- **Model ID:** `google/gemini-2.5-flash-image-preview/text-to-image`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/google/gemini-2.5-flash-image-preview/text-to-image`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/google/google-gemini-2.5-flash-image-preview-text-to-image

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `aspect_ratio`: string No - 1:1, 3:2, 2:3, 3:4, 4:3, 4:5, 5:4, 9:16, 16:9, 21:9 The aspect ratio of the generated media.
- `output_format`: string No png png, jpeg The format of the output image.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Google Gemini 2.5 Flash Image Edit

- **Model ID:** `google/gemini-2.5-flash-image/edit`
- **Operation:** `edit_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/google/gemini-2.5-flash-image/edit`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/google/google-gemini-2.5-flash-image-edit

**Request Parameters**

- `images`: array Yes [] 1 ~ 10 items List of URLs of input images for editing. Up to 10 images can be provided.
- `prompt`: string Yes - The positive prompt for the generation.
- `aspect_ratio`: string No - 1:1, 3:2, 2:3, 3:4, 4:3, 4:5, 5:4, 9:16, 16:9, 21:9 The aspect ratio of the generated media.
- `output_format`: string No jpeg jpeg, png The format of the output image.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Google Gemini 2.5 Flash Image Text To Image

- **Model ID:** `google/gemini-2.5-flash-image/text-to-image`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/google/gemini-2.5-flash-image/text-to-image`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/google/google-gemini-2.5-flash-image-text-to-image

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `aspect_ratio`: string No - 1:1, 3:2, 2:3, 3:4, 4:3, 4:5, 5:4, 9:16, 16:9, 21:9 The aspect ratio of the generated media.
- `output_format`: string No png png, jpeg The format of the output image.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Google Gemini 3 Pro Image Edit

- **Model ID:** `google/gemini-3-pro-image/edit`
- **Operation:** `edit_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/google/gemini-3-pro-image/edit`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/google/google-gemini-3-pro-image-edit

**Request Parameters**

- `images`: array Yes [] 1 ~ 10 items List of URLs of input images for editing. The maximum number of images is 10.
- `prompt`: string Yes - The positive prompt for the generation.
- `aspect_ratio`: string No - 1:1, 3:2, 2:3, 3:4, 4:3, 4:5, 5:4, 9:16, 16:9, 21:9 The aspect ratio of the generated media.
- `resolution`: string No 1k 1k, 2k, 4k The resolution of the output image.
- `output_format`: string No png png, jpeg The format of the output image.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Google Gemini 3 Pro Image Text To Image

- **Model ID:** `google/gemini-3-pro-image/text-to-image`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/google/gemini-3-pro-image/text-to-image`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/google/google-gemini-3-pro-image-text-to-image

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `aspect_ratio`: string No - 1:1, 3:2, 2:3, 3:4, 4:3, 4:5, 5:4, 9:16, 16:9, 21:9 The aspect ratio of the generated media.
- `resolution`: string No 1k 1k, 2k, 4k The resolution of the output image.
- `output_format`: string No png png, jpeg The format of the output image.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Google Imagen3

- **Model ID:** `google/imagen3`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/google/imagen3`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/google/google-imagen3

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `aspect_ratio`: string No 1:1 1:1, 16:9, 9:16, 4:3, 3:4 The aspect ratio of the generated media.
- `num_images`: integer No 1 1 ~ 4 The number of images to generate.
- `negative_prompt`: string No - The negative prompt for the generation.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Google Imagen3 Fast

- **Model ID:** `google/imagen3-fast`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/google/imagen3-fast`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/google/google-imagen3-fast

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `aspect_ratio`: string No 1:1 1:1, 16:9, 9:16, 4:3, 3:4 The aspect ratio of the generated media.
- `num_images`: integer No 1 1 ~ 4 The number of images to generate.
- `negative_prompt`: string No - The negative prompt for the generation.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Google Imagen4

- **Model ID:** `google/imagen4`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/google/imagen4`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/google/google-imagen4

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `aspect_ratio`: string No 1:1 1:1, 16:9, 9:16, 4:3, 3:4 The aspect ratio of the generated media.
- `resolution`: string No 1k 1k, 2k The target resolution of the generated media.
- `num_images`: integer No 1 1 ~ 4 The number of images to generate.
- `negative_prompt`: string No - The negative prompt for the generation.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Google Imagen4 Fast

- **Model ID:** `google/imagen4-fast`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/google/imagen4-fast`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/google/google-imagen4-fast

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `aspect_ratio`: string No 1:1 1:1, 16:9, 9:16, 4:3, 3:4 The aspect ratio of the generated media.
- `num_images`: integer No 1 1 ~ 4 The number of images to generate.
- `negative_prompt`: string No - The negative prompt for the generation.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Google Imagen4 Ultra

- **Model ID:** `google/imagen4-ultra`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/google/imagen4-ultra`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/google/google-imagen4-ultra

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `aspect_ratio`: string No 1:1 1:1, 16:9, 9:16, 4:3, 3:4 The aspect ratio of the generated media.
- `resolution`: string No 1k 1k, 2k The target resolution of the generated media.
- `num_images`: integer No 1 1 ~ 4 The number of images to generate.
- `negative_prompt`: string No - The negative prompt for the generation.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Google Nano Banana 2 Edit

- **Model ID:** `google/nano-banana-2/edit`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/google/nano-banana-2/edit`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/google/google-nano-banana-2-edit

**Request Parameters**

- `images`: array Yes [] 1 ~ 14 items List of URLs of input images for editing. The maximum number of images is 14.
- `prompt`: string Yes - The positive prompt for the generation.
- `aspect_ratio`: string No - 1:1, 3:2, 2:3, 3:4, 4:3, 4:5, 5:4, 9:16, 16:9, 21:9, 1:4, 4:1, 1:8, 8:1 The aspect ratio of the generated media.
- `resolution`: string No 1k 0.5k, 1k, 2k, 4k The resolution of the output image.
- `enable_web_search`: boolean No false - If enabled, the model will use web search to enhance the generation with real-time information.
- `enable_image_search`: boolean No false - If enabled, the model will use image search to enhance the generation with real-time information.
- `output_format`: string No png png, jpeg The format of the output image.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API. 
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Google Nano Banana 2 Edit Fast

- **Model ID:** `google/nano-banana-2/edit-fast`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/google/nano-banana-2/edit-fast`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/google/google-nano-banana-2-edit-fast

**Request Parameters**

- `images`: array Yes [] 1 ~ 14 items List of URLs of input images for editing. The maximum number of images is 14.
- `prompt`: string Yes - The positive prompt for the generation.
- `aspect_ratio`: string No - 1:1, 3:2, 2:3, 3:4, 4:3, 4:5, 5:4, 9:16, 16:9, 21:9, 1:4, 4:1, 1:8, 8:1 The aspect ratio of the generated media.
- `resolution`: string No 2k 2k, 4k The resolution of the output image.
- `enable_web_search`: boolean No false - If enabled, the model will use web search to enhance the generation with real-time information.
- `output_format`: string No png png, jpeg The format of the output image.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API. 
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Google Nano Banana 2 Text To Image

- **Model ID:** `google/nano-banana-2/text-to-image`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/google/nano-banana-2/text-to-image`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/google/google-nano-banana-2-text-to-image

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `aspect_ratio`: string No - 1:1, 3:2, 2:3, 3:4, 4:3, 4:5, 5:4, 9:16, 16:9, 21:9, 1:4, 4:1, 1:8, 8:1 The aspect ratio of the generated media.
- `resolution`: string No 1k 0.5k, 1k, 2k, 4k The resolution of the output image.
- `enable_web_search`: boolean No false - If enabled, the model will use web search to enhance the generation with real-time information.
- `enable_image_search`: boolean No false - If enabled, the model will use image search to enhance the generation with real-time information.
- `output_format`: string No png png, jpeg The format of the output image.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API. 
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Google Nano Banana 2 Text To Image Fast

- **Model ID:** `google/nano-banana-2/text-to-image-fast`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/google/nano-banana-2/text-to-image-fast`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/google/google-nano-banana-2-text-to-image-fast

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `aspect_ratio`: string No - 1:1, 3:2, 2:3, 3:4, 4:3, 4:5, 5:4, 9:16, 16:9, 21:9, 1:4, 4:1, 1:8, 8:1 The aspect ratio of the generated media.
- `resolution`: string No 2k 2k, 4k The resolution of the output image.
- `enable_web_search`: boolean No false - If enabled, the model will use web search to enhance the generation with real-time information.
- `output_format`: string No png png, jpeg The format of the output image.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API. 
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Google Nano Banana Pro Edit

- **Model ID:** `google/nano-banana-pro/edit`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/google/nano-banana-pro/edit`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/google/google-nano-banana-pro-edit

**Request Parameters**

- `images`: array Yes [] 1 ~ 14 items List of URLs of input images for editing. The maximum number of images is 14.
- `prompt`: string Yes - The positive prompt for the generation.
- `aspect_ratio`: string No - 1:1, 3:2, 2:3, 3:4, 4:3, 4:5, 5:4, 9:16, 16:9, 21:9 The aspect ratio of the generated media.
- `resolution`: string No 1k 1k, 2k, 4k The resolution of the output image.
- `output_format`: string No png png, jpeg The format of the output image.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API. 
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Google Nano Banana Pro Edit Multi

- **Model ID:** `google/nano-banana-pro/edit-multi`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/google/nano-banana-pro/edit-multi`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/google/google-nano-banana-pro-edit-multi

**Request Parameters**

- `images`: array Yes [] 1 ~ 14 items List of URLs of input images for editing. The maximum number of images is 14.
- `prompt`: string Yes - The positive prompt for the generation.
- `aspect_ratio`: string No - 3:2, 2:3, 3:4, 4:3 The aspect ratio of the generated media.
- `num_images`: integer No 2 2 The number of images to generate.
- `output_format`: string No png png, jpeg The format of the output image.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API. 
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Google Nano Banana Pro Edit Ultra

- **Model ID:** `google/nano-banana-pro/edit-ultra`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/google/nano-banana-pro/edit-ultra`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/google/google-nano-banana-pro-edit-ultra

**Request Parameters**

- `images`: array Yes [] 1 ~ 14 items List of URLs of input images for editing. The maximum number of images is 14.
- `prompt`: string Yes - The positive prompt for the generation.
- `aspect_ratio`: string No - 1:1, 3:2, 2:3, 3:4, 4:3, 4:5, 5:4, 9:16, 16:9, 21:9 The aspect ratio of the generated media.
- `resolution`: string No 4k 4k, 8k The resolution of the output image.
- `output_format`: string No png png, jpeg The format of the output image.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API. 
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Google Nano Banana Pro Text To Image

- **Model ID:** `google/nano-banana-pro/text-to-image`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/google/nano-banana-pro/text-to-image`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/google/google-nano-banana-pro-text-to-image

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `aspect_ratio`: string No - 1:1, 3:2, 2:3, 3:4, 4:3, 4:5, 5:4, 9:16, 16:9, 21:9 The aspect ratio of the generated media.
- `resolution`: string No 1k 1k, 2k, 4k The resolution of the output image.
- `output_format`: string No png png, jpeg The format of the output image.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API. 
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Google Nano Banana Pro Text To Image Multi

- **Model ID:** `google/nano-banana-pro/text-to-image-multi`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/google/nano-banana-pro/text-to-image-multi`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/google/google-nano-banana-pro-text-to-image-multi

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `aspect_ratio`: string Yes 3:2 3:2, 2:3, 3:4, 4:3 The aspect ratio of the generated media.
- `num_images`: integer No 2 2 The number of images to generate.
- `output_format`: string No png png, jpeg The format of the output image.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API. 
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Google Nano Banana Pro Text To Image Ultra

- **Model ID:** `google/nano-banana-pro/text-to-image-ultra`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/google/nano-banana-pro/text-to-image-ultra`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/google/google-nano-banana-pro-text-to-image-ultra

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `aspect_ratio`: string No - 1:1, 3:2, 2:3, 3:4, 4:3, 4:5, 5:4, 9:16, 16:9, 21:9 The aspect ratio of the generated media.
- `resolution`: string No 4k 4k, 8k The resolution of the output image.
- `output_format`: string No png png, jpeg The format of the output image.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API. 
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Google Nano Banana Edit

- **Model ID:** `google/nano-banana/edit`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/google/nano-banana/edit`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/google/google-nano-banana-edit

**Request Parameters**

- `images`: array Yes [] 1 ~ 10 items List of URLs of input images for editing. The maximum number of images is 10.
- `prompt`: string Yes - The positive prompt for the generation.
- `aspect_ratio`: string No - 1:1, 3:2, 2:3, 3:4, 4:3, 4:5, 5:4, 9:16, 16:9, 21:9 The aspect ratio of the generated media.
- `output_format`: string No png png, jpeg The format of the output image.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API. 
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Google Nano Banana Text To Image

- **Model ID:** `google/nano-banana/text-to-image`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/google/nano-banana/text-to-image`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/google/google-nano-banana-text-to-image

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `aspect_ratio`: string No - 1:1, 3:2, 2:3, 3:4, 4:3, 4:5, 5:4, 9:16, 16:9, 21:9 The aspect ratio of the generated media.
- `output_format`: string No png png, jpeg The format of the output image.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API. 
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Google Veo2

- **Model ID:** `google/veo2`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/google/veo2`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/google/google-veo2

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `aspect_ratio`: string No 16:9 16:9, 9:16 The aspect ratio of the generated media.
- `duration`: integer No 5 5, 6, 7, 8 The duration of the generated media in seconds.
- `resolution`: string No 720p 720p Video resolution.
- `enable_prompt_expansion`: boolean No true - If set to true, the prompt optimizer will be enabled.
- `negative_prompt`: string No - The negative prompt for the generation.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation.

### Google Veo3

- **Model ID:** `google/veo3`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/google/veo3`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/google/google-veo3

**Request Parameters**

- `prompt`: string Yes - Text prompt for generation; Positive text prompt.
- `aspect_ratio`: string No 16:9 16:9, 9:16 Aspect ratio of the video.
- `duration`: integer No 8 8, 4, 6 The duration of the generated media in seconds.
- `resolution`: string No 720p 720p, 1080p Video resolution.
- `generate_audio`: boolean No true - Whether to generate audio.
- `negative_prompt`: string No - Negative prompt for the generation.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation.

### Google Veo3 Fast

- **Model ID:** `google/veo3-fast`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/google/veo3-fast`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/google/google-veo3-fast

**Request Parameters**

- `prompt`: string Yes - Text prompt for generation; Positive text prompt.
- `aspect_ratio`: string No 16:9 16:9, 9:16 Aspect ratio of the video.
- `duration`: integer No 8 8, 4, 6 The duration of the generated media in seconds.
- `resolution`: string No 720p 720p, 1080p Video resolution.
- `generate_audio`: boolean No true - Whether to generate audio.
- `negative_prompt`: string No - Negative prompt for the generation.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation.

### Higgsfield Soul Image To Image

- **Model ID:** `higgsfield/soul/image-to-image`
- **Operation:** `edit_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/higgsfield/soul/image-to-image`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/higgsfield/higgsfield-soul-image-to-image

**Request Parameters**

- `image`: string Yes -
- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string Yes 1152*2048 960*1696, 1088*1632, 1152*1536, 1152*2048, 1536*1536, 1536*1152, 1536*2048, 1344*2016, 1632*1088, 1696*960, 2016*1344, 2048*1152, 2048*1536 The size of the generated media in pixels (width*height).
- `style`: string No Creatures Creatures, Medieval, Spotlight, Giant People, Red balloon, green editorial, Subway, Library, Realistic, DigitalCam, Grillz Selfie, Bleached Brows, Sitting on the Street, Crossing the street, Angel Wings, Duplicate, Quiet
- `strength`: number No 1 0.00 ~ 1.00 The strength to use for the style.
- `quality`: string No medium medium, high The resolution of the output image.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Ideogram AI Ideogram Character

- **Model ID:** `ideogram-ai/ideogram-character`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/ideogram-ai/ideogram-character`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/ideogram-ai/ideogram-ai-ideogram-character

**Request Parameters**

- `image`: string Yes - An image to use as a character reference.
- `prompt`: string Yes - The positive prompt for the generation.
- `style`: string No Auto Auto, Fiction, Realistic The character style type. Auto, Fiction, or Realistic.
- `rendering_speed`: string No Default Default, Turbo, Quality Rendering speed. Turbo for faster and cheaper generation, quality for higher quality and more expensive generation, default for balanced.
- `aspect_ratio`: string No 1:1 1:1, 16:9, 9:16, 4:3, 3:4 The aspect ratio of the generated media.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Ideogram AI Ideogram V2

- **Model ID:** `ideogram-ai/ideogram-v2`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/ideogram-ai/ideogram-v2`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/ideogram-ai/ideogram-ai-ideogram-v2

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `image`: string No - The image to generate an image from.
- `mask_image`: string No - The mask image tells the model where to generate new pixels (white) and where to preserve the original image (black). It acts as a stencil or guide for targeted image editing.
- `style`: string No Auto Auto, General, Realistic, Design, Render 3D, Anime The style of the generated image.
- `aspect_ratio`: string No 1:1 1:1, 16:9, 9:16, 4:3, 3:4 The aspect ratio of the generated media.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Ideogram AI Ideogram V2 Turbo

- **Model ID:** `ideogram-ai/ideogram-v2-turbo`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/ideogram-ai/ideogram-v2-turbo`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/ideogram-ai/ideogram-ai-ideogram-v2-turbo

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `image`: string No - The image to generate an image from.
- `mask_image`: string No - The mask image tells the model where to generate new pixels (white) and where to preserve the original image (black). It acts as a stencil or guide for targeted image editing.
- `style`: string No Auto Auto, General, Realistic, Design, Render 3D, Anime The style of the generated image.
- `aspect_ratio`: string No 1:1 1:1, 16:9, 9:16, 4:3, 3:4 The aspect ratio of the generated media.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Ideogram AI Ideogram V2A

- **Model ID:** `ideogram-ai/ideogram-v2a`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/ideogram-ai/ideogram-v2a`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/ideogram-ai/ideogram-ai-ideogram-v2a

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `image`: string No - The image to generate an image from.
- `mask_image`: string No - The mask image tells the model where to generate new pixels (white) and where to preserve the original image (black). It acts as a stencil or guide for targeted image editing.
- `style`: string No Auto Auto, General, Realistic, Design, Render 3D, Anime The style of the generated image.
- `aspect_ratio`: string No 1:1 1:1, 16:9, 9:16, 4:3, 3:4 The aspect ratio of the generated media.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Ideogram AI Ideogram V2A Turbo

- **Model ID:** `ideogram-ai/ideogram-v2a-turbo`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/ideogram-ai/ideogram-v2a-turbo`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/ideogram-ai/ideogram-ai-ideogram-v2a-turbo

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `image`: string No - The image to generate an image from.
- `mask_image`: string No - The mask image tells the model where to generate new pixels (white) and where to preserve the original image (black). It acts as a stencil or guide for targeted image editing.
- `style`: string No Auto Auto, General, Realistic, Design, Render 3D, Anime The style of the generated image.
- `aspect_ratio`: string No 1:1 1:1, 16:9, 9:16, 4:3, 3:4 The aspect ratio of the generated media.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Ideogram AI Ideogram V3 Balanced

- **Model ID:** `ideogram-ai/ideogram-v3-balanced`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/ideogram-ai/ideogram-v3-balanced`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/ideogram-ai/ideogram-ai-ideogram-v3-balanced

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `image`: string No - The image to generate an image from.
- `mask_image`: string No - The mask image tells the model where to generate new pixels (white) and where to preserve the original image (black). It acts as a stencil or guide for targeted image editing.
- `style`: string No Auto Auto, General, Realistic, Design The style of the generated image.
- `aspect_ratio`: string No 1:1 1:1, 16:9, 9:16, 4:3, 3:4 The aspect ratio of the generated media.
- `reference_images`: array No - - A list of images to use as style references.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Ideogram AI Ideogram V3 Quality

- **Model ID:** `ideogram-ai/ideogram-v3-quality`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/ideogram-ai/ideogram-v3-quality`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/ideogram-ai/ideogram-ai-ideogram-v3-quality

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `image`: string No - The image to generate an image from.
- `mask_image`: string No - The mask image tells the model where to generate new pixels (white) and where to preserve the original image (black). It acts as a stencil or guide for targeted image editing.
- `style`: string No Auto Auto, General, Realistic, Design The style of the generated image.
- `aspect_ratio`: string No 1:1 1:1, 16:9, 9:16, 4:3, 3:4 The aspect ratio of the generated media.
- `reference_images`: array No - - A list of images to use as style references.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Ideogram AI Ideogram V3 Turbo

- **Model ID:** `ideogram-ai/ideogram-v3-turbo`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/ideogram-ai/ideogram-v3-turbo`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/ideogram-ai/ideogram-ai-ideogram-v3-turbo

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `image`: string No - The image to generate an image from.
- `mask_image`: string No - The mask image tells the model where to generate new pixels (white) and where to preserve the original image (black). It acts as a stencil or guide for targeted image editing.
- `style`: string No Auto Auto, General, Realistic, Design The style of the generated image.
- `aspect_ratio`: string No 1:1 1:1, 16:9, 9:16, 4:3, 3:4 The aspect ratio of the generated media.
- `reference_images`: array No - - A list of images to use as style references.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Ideogram AI Ideogram V3 Generate Transparent

- **Model ID:** `ideogram-ai/ideogram-v3/generate-transparent`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/ideogram-ai/ideogram-v3/generate-transparent`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/ideogram-ai/ideogram-ai-ideogram-v3-generate-transparent

**Request Parameters**

- `prompt`: string Yes - Text description of the image to generate with transparent background.
- `aspect_ratio`: string No 1:1 1:1, 16:9, 9:16, 4:3, 3:4, 3:2, 2:3 The aspect ratio of the generated image.
- `rendering_speed`: string No balanced flash, turbo, balanced, quality Controls the quality-speed tradeoff. flash is fastest, quality produces the best results.

### Ideogram AI Ideogram V3 Remove Text

- **Model ID:** `ideogram-ai/ideogram-v3/remove-text`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/ideogram-ai/ideogram-v3/remove-text`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/ideogram-ai/ideogram-ai-ideogram-v3-remove-text

**Request Parameters**

- `image`: string Yes - The flat graphic image to layerize. Supports JPEG, PNG, or WebP (max 10MB).

### Image Effects American Comic Style

- **Model ID:** `image-effects/american-comic-style`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/image-effects/american-comic-style`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/image-effects/image-effects-american-comic-style

**Request Parameters**

- `image`: string Yes - The image to edit, can be a URL or base64 encoded image.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Image Effects Angel Figurine

- **Model ID:** `image-effects/angel-figurine`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/image-effects/angel-figurine`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/image-effects/image-effects-angel-figurine

**Request Parameters**

- `image`: string Yes - The image to edit, can be a URL or base64 encoded image.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Image Effects Cyberpunk

- **Model ID:** `image-effects/cyberpunk`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/image-effects/cyberpunk`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/image-effects/image-effects-cyberpunk

**Request Parameters**

- `image`: string Yes - The image to edit, can be a URL or base64 encoded image.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Image Effects Exotic Charm

- **Model ID:** `image-effects/exotic-charm`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/image-effects/exotic-charm`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/image-effects/image-effects-exotic-charm

**Request Parameters**

- `image`: string Yes -
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Image Effects Felt Keychain

- **Model ID:** `image-effects/felt-keychain`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/image-effects/felt-keychain`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/image-effects/image-effects-felt-keychain

**Request Parameters**

- `image`: string Yes - The image to edit, can be a URL or base64 encoded image.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Image Effects In The Stadium

- **Model ID:** `image-effects/in-the-stadium`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/image-effects/in-the-stadium`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/image-effects/image-effects-in-the-stadium

**Request Parameters**

- `image`: string Yes - The image to edit, can be a URL or base64 encoded image.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Image Effects Lying In Fluffy Belly

- **Model ID:** `image-effects/lying-in-fluffy-belly`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/image-effects/lying-in-fluffy-belly`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/image-effects/image-effects-lying-in-fluffy-belly

**Request Parameters**

- `image`: string Yes - The image to edit, can be a URL or base64 encoded image.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Kwaivgi Kling Effects

- **Model ID:** `kwaivgi/kling-effects`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/kwaivgi/kling-effects`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/kwaivgi/kwaivgi-kling-effects

**Request Parameters**

- `image`: string Yes - Image URL or Base64 encoding, in the format of data:image/png;base64,...
- `effect_scene`: string Yes - firework_2026, glamour_photo_shoot, box_of_joy, first_toast_of_the_year, my_santa_pic, santa_gift, steampunk_christmas, snowglobe, ornament_crash, santa_express, instant_christmas, particle_santa_surround, coronation_of_frost, 

### Kwaivgi Kling Elements

- **Model ID:** `kwaivgi/kling-elements`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/kwaivgi/kling-elements`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/kwaivgi/kwaivgi-kling-elements

**Request Parameters**

- `name`: string Yes - - Element name, It cannot exceed 20 characters.
- `description`: string Yes - - Element description, It cannot exceed 100 characters.
- `image`: string Yes - Front reference image, The size of the image file should not exceed 10MB, and the width and height dimensions of the image should be no less than 300px.
- `voice_id`: string No - - The voice ID of element can be bound to existing tone colors in the tone library.
- `element_refer_list`: array Yes - 1 ~ 3 items Other reference list of the element.
- `tag_list`: array No - - Configure tags for the element.

### Kwaivgi Kling Elements Advanced

- **Model ID:** `kwaivgi/kling-elements-advanced`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/kwaivgi/kling-elements-advanced`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/kwaivgi/kwaivgi-kling-elements-advanced

**Request Parameters**

- `name`: string Yes - - Element name, It cannot exceed 20 characters.
- `description`: string Yes - - Element description, It cannot exceed 100 characters.
- `reference_type`: string No image_refer image_refer, video_refer Reference method.
- `frontal_image`: string No - -
- `refer_images`: array No [""] 1 ~ 3 items Other reference list of the element.
- `element_video_list`: array No - 1 ~ 1 items Other reference list of the element.
- `voice_id`: string No - - The voice ID of element can be bound to existing tone colors in the tone library.
- `tag_list`: array No - - Configure tags for the element.

### Kwaivgi Kling Image O1

- **Model ID:** `kwaivgi/kling-image-o1`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/kwaivgi/kling-image-o1`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/kwaivgi/kwaivgi-kling-image-o1

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `images`: array No [] - Including reference images of the element, scene, style, etc.max 10
- `aspect_ratio`: string No 1:1 16:9, 9:16, 1:1, 4:3, 3:4, 3:2, 2:3, 21:9, auto The aspect ratio of the generated image.
- `resolution`: string No 1k 1k, 2k Image generation resolution
- `num_images`: integer No 1 1 ~ 9 The number of images to generate.

### Kwaivgi Kling Image O3 Edit

- **Model ID:** `kwaivgi/kling-image-o3/edit`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/kwaivgi/kling-image-o3/edit`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/kwaivgi/kwaivgi-kling-image-o3-edit

**Request Parameters**

- `images`: array Yes [] - Reference images (max 10). Use @Image1, @Image2 in prompt to reference.
- `prompt`: string Yes - Text prompt for image generation. Reference images using @Image1, @Image2, etc.
- `aspect_ratio`: string No auto auto, 16:9, 9:16, 1:1, 4:3, 3:4, 3:2, 2:3, 21:9 Aspect ratio of the generated image.
- `resolution`: string No 1k 1k, 2k, 4k Image generation resolution.
- `num_images`: integer No 1 1 ~ 9 Number of images to generate.
- `output_format`: string No png png, jpeg, webp Output image format.
- `shot_type`: string No customize customize, intelligent Shot type for the generation.

### Kwaivgi Kling Image O3 Text To Image

- **Model ID:** `kwaivgi/kling-image-o3/text-to-image`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/kwaivgi/kling-image-o3/text-to-image`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/kwaivgi/kwaivgi-kling-image-o3-text-to-image

**Request Parameters**

- `prompt`: string Yes - Text prompt for image generation.
- `aspect_ratio`: string No 16:9 16:9, 9:16, 1:1, 4:3, 3:4, 3:2, 2:3, 21:9 Aspect ratio of the generated image.
- `resolution`: string No 1k 1k, 2k, 4k Image generation resolution.
- `num_images`: integer No 1 1 ~ 9 Number of images to generate.
- `output_format`: string No png png, jpeg, webp Output image format.
- `shot_type`: string No customize customize, intelligent Shot type for the generation.

### Kwaivgi Kling Image V3 Edit

- **Model ID:** `kwaivgi/kling-image-v3/edit`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/kwaivgi/kling-image-v3/edit`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/kwaivgi/kwaivgi-kling-image-v3-edit

**Request Parameters**

- `image`: string Yes - Reference image for image-to-image generation.
- `prompt`: string Yes - Text prompt for image generation.
- `aspect_ratio`: string No 16:9 16:9, 9:16, 1:1, 4:3, 3:4, 3:2, 2:3, 21:9 Aspect ratio of the generated image.
- `resolution`: string No 1k 1k, 2k Image generation resolution.
- `num_images`: integer No 1 1 ~ 9 Number of images to generate.
- `output_format`: string No png png, jpeg, webp Output image format.
- `shot_type`: string No customize customize, intelligent Shot type for the generation.

### Kwaivgi Kling Image V3 Text To Image

- **Model ID:** `kwaivgi/kling-image-v3/text-to-image`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/kwaivgi/kling-image-v3/text-to-image`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/kwaivgi/kwaivgi-kling-image-v3-text-to-image

**Request Parameters**

- `prompt`: string Yes - Text prompt for image generation.
- `aspect_ratio`: string No 16:9 16:9, 9:16, 1:1, 4:3, 3:4, 3:2, 2:3, 21:9 Aspect ratio of the generated image.
- `resolution`: string No 1k 1k, 2k Image generation resolution.
- `num_images`: integer No 1 1 ~ 9 Number of images to generate.
- `output_format`: string No png png, jpeg, webp Output image format.
- `shot_type`: string No customize customize, intelligent Shot type for the generation.

### Kwaivgi Kling V1 AI Multi Shot

- **Model ID:** `kwaivgi/kling-v1/ai-multi-shot`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/kwaivgi/kling-v1/ai-multi-shot`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/kwaivgi/kwaivgi-kling-v1-ai-multi-shot

**Request Parameters**

- `image`: string Yes - Supported image formats:.jpg /.jpeg /.png The size of the image file should not exceed 10MB, the width and height of the image should be no less than 300px, and the aspect ratio of the image should be between 1:2.5 and 2.5:1
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Kwaivgi Kling V2.6 Pro Motion Control

- **Model ID:** `kwaivgi/kling-v2.6-pro/motion-control`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/kwaivgi/kling-v2.6-pro/motion-control`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/kwaivgi/kwaivgi-kling-v2.6-pro-motion-control

**Request Parameters**

- `image`: string Yes - Supported image formats:.jpg /.jpeg /.png The size of the image file should not exceed 10MB, the width and height of the image should be no less than 300px, and the aspect ratio of the image should be between 1:2.5 and 2.5:1
- `video`: string Yes - Supported video formats:.mp4/.mov The size of the video file should not exceed 10MB, the width and height of the video should be no less than 300px, and the aspect ratio of the video should be between 1:2.5 and 2.5:1
- `character_orientation`: string Yes - image, video The duration of the generated media in seconds.
- `prompt`: string No - The positive prompt for the generation.
- `negative_prompt`: string No - The negative prompt for the generation.
- `keep_original_sound`: boolean No true - Whether to retain the original video sound

### Kwaivgi Kling V2.6 Std Motion Control

- **Model ID:** `kwaivgi/kling-v2.6-std/motion-control`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/kwaivgi/kling-v2.6-std/motion-control`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/kwaivgi/kwaivgi-kling-v2.6-std-motion-control

**Request Parameters**

- `image`: string Yes - Supported image formats:.jpg /.jpeg /.png The size of the image file should not exceed 10MB, the width and height of the image should be no less than 300px, and the aspect ratio of the image should be between 1:2.5 and 2.5:1
- `video`: string Yes - Supported video formats:.mp4/.mov The size of the video file should not exceed 10MB, the width and height of the video should be no less than 300px, and the aspect ratio of the video should be between 1:2.5 and 2.5:1
- `character_orientation`: string Yes - image, video Generate the orientation of the characters in the video, which can be selected to match the image or the video.
- `prompt`: string No - The positive prompt for the generation.
- `negative_prompt`: string No - The negative prompt for the generation.
- `keep_original_sound`: boolean No true - Whether to retain the original video sound

### Kwaivgi Kling V3.0 Pro Motion Control

- **Model ID:** `kwaivgi/kling-v3.0-pro/motion-control`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/kwaivgi/kling-v3.0-pro/motion-control`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/kwaivgi/kwaivgi-kling-v3.0-pro-motion-control

**Request Parameters**

- `image`: string Yes - Supported image formats:.jpg /.jpeg /.png The size of the image file should not exceed 10MB, the width and height of the image should be no less than 300px, and the aspect ratio of the image should be between 1:2.5 and 2.5:1
- `video`: string Yes - The duration range of the uploaded motion reference is from 3 to 30 seconds, in which the generated video length will align with the duration of the uploaded video.
- `shot_type`: string No customize customize, intelligent Shot type for the generation.
- `element_list`: array No - - Element reference list.
- `character_orientation`: string No - image, video The duration of the generated media in seconds.
- `prompt`: string No - The positive prompt for the generation.
- `negative_prompt`: string No - The negative prompt for the generation.
- `keep_original_sound`: boolean No true - Whether to retain the original video sound

### Kwaivgi Kling V3.0 Std Motion Control

- **Model ID:** `kwaivgi/kling-v3.0-std/motion-control`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/kwaivgi/kling-v3.0-std/motion-control`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/kwaivgi/kwaivgi-kling-v3.0-std-motion-control

**Request Parameters**

- `image`: string Yes - Supported image formats:.jpg /.jpeg /.png The size of the image file should not exceed 10MB, the width and height of the image should be no less than 300px, and the aspect ratio of the image should be between 1:2.5 and 2.5:1
- `video`: string Yes - The duration range of the uploaded motion reference is from 3 to 30 seconds, in which the generated video length will align with the duration of the uploaded video.
- `shot_type`: string No customize customize, intelligent Shot type for the generation.
- `element_list`: array No - - Element reference list.
- `character_orientation`: string No - image, video The duration of the generated media in seconds.
- `prompt`: string No - The positive prompt for the generation.
- `negative_prompt`: string No - The negative prompt for the generation.
- `keep_original_sound`: boolean No true - Whether to retain the original video sound

### Leonardoai Lucid Origin

- **Model ID:** `leonardoai/lucid-origin`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/leonardoai/lucid-origin`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/leonardoai/leonardoai-lucid-origin

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `aspect_ratio`: string No 16:9 1:1, 16:9, 9:16, 3:2, 2:3, 4:5, 5:4, 3:4, 4:3, 2:1, 1:2, 3:1, 1:3 The aspect ratio of the generated media.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Leonardoai Motion 2.0

- **Model ID:** `leonardoai/motion-2.0`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/leonardoai/motion-2.0`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/leonardoai/leonardoai-motion-2.0

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `image`: string No - The image to be used for image generation.
- `aspect_ratio`: string No 16:9 16:9, 9:16 The aspect ratio of the generated media.

### Leonardoai Phoenix 1.0

- **Model ID:** `leonardoai/phoenix-1.0`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/leonardoai/phoenix-1.0`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/leonardoai/leonardoai-phoenix-1.0

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `aspect_ratio`: string No 16:9 1:1, 16:9, 9:16, 3:2, 2:3, 4:5, 5:4, 3:4, 4:3, 2:1, 1:2, 3:1, 1:3 The aspect ratio of the generated media.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Lightricks Ltx 2 Retake

- **Model ID:** `lightricks/ltx-2-retake`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/lightricks/ltx-2-retake`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/lightricks/lightricks-ltx-2-retake

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `video`: string Yes - The video for generating the output.
- `mode`: string No replace_audio_and_video replace_audio_and_video, replace_audio, replace_video Mode of operation.

### Luma Photon

- **Model ID:** `luma/photon`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/luma/photon`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/luma/luma-photon

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Luma Photon Flash

- **Model ID:** `luma/photon-flash`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/luma/photon-flash`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/luma/luma-photon-flash

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Luma Photon Flash Modify

- **Model ID:** `luma/photon-flash-modify`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/luma/photon-flash-modify`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/luma/luma-photon-flash-modify

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `image`: string No - The image to generate an image from.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Luma Photon Modify

- **Model ID:** `luma/photon-modify`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/luma/photon-modify`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/luma/luma-photon-modify

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `image`: string No - The image to generate an image from.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Midjourney Text To Image

- **Model ID:** `midjourney/text-to-image`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/midjourney/text-to-image`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/midjourney/midjourney-text-to-image

**Request Parameters**

- `prompt`: string Yes - The text prompt describing the image you want to generate.
- `sref`: string No - - URL of the image to use as a reference for the image generation.
- `aspect_ratio`: string No 1:1 1:1, 9:16, 16:9, 4:3, 3:4, 2:3, 3:2, 9:21, 21:9 The aspect ratio of the generated media.
- `quality`: number No 1 0.25, 0.5, 1, 2 Use the quality parameter to control image detail and processing time.
- `stylize`: integer No - 0 ~ 1000 Use the stylize parameter to control the artistic style in the image (0-1000).
- `chaos`: integer No - 0 ~ 100 Use the chaos parameter to add variety to your image results (0-100). Higher values produce more unusual and unexpected results.
- `weird`: integer No - 0 ~ 3000 Use the weird parameter to make your images quirky and unconventional (0-3000).
- `version`: string No 7 6, 6.1, 7 Use the version parameter to explore and switch between Midjourney model versions.
- `niji`: string No close 0, 5, 6, close Use the Niji model focused on anime and Eastern aesthetics.
- `seed`: integer No -1 -1 ~ 2147483647 Use the seed parameter for testing and experimentation. Use the same seed and prompt to get similar results.
- `enable_base64_output`: boolean No false - The random seed to use for the generation.

### Minimax Hailuo 02 Fast

- **Model ID:** `minimax/hailuo-02/fast`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/minimax/hailuo-02/fast`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/minimax/minimax-hailuo-02-fast

**Request Parameters**

- `image`: string Yes - The model generates video with the picture passed in as the first frame.Base64 encoded strings in data:image/jpeg; base64,{data} format for incoming images, or URLs accessible via the public network. The uploaded image needs to
- `prompt`: string No - The positive prompt for the generation.
- `duration`: integer No 6 6, 10 The duration of the generated media in seconds.
- `enable_prompt_expansion`: boolean No true - The model automatically optimizes incoming prompts to enhance output quality. This also activates the safety checker, which ensures content safety by detecting and filtering potential risks.
- `go_fast`: boolean No true - Prioritize faster video generation speed with a moderate trade-off in visual quality

### Minimax Hailuo 02 Pro

- **Model ID:** `minimax/hailuo-02/pro`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/minimax/hailuo-02/pro`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/minimax/minimax-hailuo-02-pro

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `image`: string No - The model generates video with the picture passed in as the first frame.Base64 encoded strings in data:image/jpeg; base64,{data} format for incoming images, or URLs accessible via the public network. The uploaded image needs to 
- `end_image`: string No - - The model generates video with the picture passed in as the first frame.Base64 encoded strings in data:image/jpeg; base64,{data} format for incoming images, or URLs accessible via the public network. The uploaded image needs t
- `duration`: integer No 6 6 The duration of the generated media in seconds.
- `enable_prompt_expansion`: boolean No false - The model automatically optimizes incoming prompts to enhance output quality. This also activates the safety checker, which ensures content safety by detecting and filtering potential risks.

### Minimax Hailuo 02 Standard

- **Model ID:** `minimax/hailuo-02/standard`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/minimax/hailuo-02/standard`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/minimax/minimax-hailuo-02-standard

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `image`: string No - The model generates video with the picture passed in as the first frame.Base64 encoded strings in data:image/jpeg; base64,{data} format for incoming images, or URLs accessible via the public network. The uploaded image needs to 
- `end_image`: string No - - The model generates video with the picture passed in as the last frame.Base64 encoded strings in data:image/jpeg; base64,{data} format for incoming images, or URLs accessible via the public network. The uploaded image needs to
- `duration`: integer No 6 6, 10 The duration of the generated media in seconds.
- `enable_prompt_expansion`: boolean No false - The model automatically optimizes incoming prompts to enhance output quality. This also activates the safety checker, which ensures content safety by detecting and filtering potential risks.

### Minimax Hailuo 2.3 Fast

- **Model ID:** `minimax/hailuo-2.3/fast`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/minimax/hailuo-2.3/fast`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/minimax/minimax-hailuo-2.3-fast

**Request Parameters**

- `image`: string Yes - The model generates video with the picture passed in as the first frame.Base64 encoded strings in data:image/jpeg; base64,{data} format for incoming images, or URLs accessible via the public network. The uploaded image needs to
- `prompt`: string No - The positive prompt for the generation.
- `duration`: integer No 6 6, 10 The duration of the generated media in seconds.
- `enable_prompt_expansion`: boolean No true - The model automatically optimizes incoming prompts to enhance output quality. This also activates the safety checker, which ensures content safety by detecting and filtering potential risks.
- `go_fast`: boolean No true - Prioritize faster video generation speed with a moderate trade-off in visual quality

### Minimax Hailuo 2.3 Fast Pro

- **Model ID:** `minimax/hailuo-2.3/fast-pro`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/minimax/hailuo-2.3/fast-pro`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/minimax/minimax-hailuo-2.3-fast-pro

**Request Parameters**

- `image`: string Yes - The model generates video with the picture passed in as the first frame.Base64 encoded strings in data:image/jpeg; base64,{data} format for incoming images, or URLs accessible via the public network. The uploaded image needs to
- `prompt`: string No - The positive prompt for the generation.
- `duration`: integer No 6 6 The duration of the generated media in seconds.
- `enable_prompt_expansion`: boolean No true - The model automatically optimizes incoming prompts to enhance output quality. This also activates the safety checker, which ensures content safety by detecting and filtering potential risks.
- `go_fast`: boolean No true - Prioritize faster video generation speed with a moderate trade-off in visual quality

### Minimax Image 01 Image To Image

- **Model ID:** `minimax/image-01/image-to-image`
- **Operation:** `edit_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/minimax/image-01/image-to-image`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/minimax/minimax-image-01-image-to-image

**Request Parameters**

- `prompt`: string Yes - Text description of the image, max length 1500 characters.
- `image`: string No - Reference image file. Supports public URLs or Base64-encoded
- `size`: string No 1024*1024 512 ~ 2048 per dimension Specify the width and height pixel values of the generated image.
- `num_images`: integer No 1 1 ~ 9 The number of images to generate.
- `seed`: integer No - -1 ~ 2147483647 Random seed. Using the same seed and parameters produces reproducible images. If not provided, a random seed is generated for each image.
- `prompt_optimizer`: boolean No false - Enable automatic optimization of prompt.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Minimax Image 01 Text To Image

- **Model ID:** `minimax/image-01/text-to-image`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/minimax/image-01/text-to-image`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/minimax/minimax-image-01-text-to-image

**Request Parameters**

- `prompt`: string Yes - Text description of the image, max length 1500 characters.
- `size`: string No 1024*1024 512 ~ 2048 per dimension Specify the width and height pixel values of the generated image. Must be divisible by 8
- `num_images`: integer No 1 1 ~ 9 The number of images to generate.
- `seed`: integer No - -1 ~ 2147483647 Random seed. Using the same seed and parameters produces reproducible images. If not provided, a random seed is generated for each image.
- `prompt_optimizer`: boolean No false - Enable automatic optimization of prompt.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Nvidia Chrono Edit

- **Model ID:** `nvidia/chrono-edit`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/nvidia/chrono-edit`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/nvidia/nvidia-chrono-edit

**Request Parameters**

- `image`: string Yes - The image to generate an image from.
- `prompt`: string Yes - The positive prompt for the generation.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `output_format`: string No jpeg jpeg, png The format of the output image.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Nvidia Nemotron 3 Nano Omni Text

- **Model ID:** `nvidia/nemotron-3-nano-omni/text`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/nvidia/nemotron-3-nano-omni/text`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/nvidia/nvidia-nemotron-3-nano-omni-text

**Request Parameters**

- `prompt`: string Yes - Text prompt to send to the model. English only.
- `system_prompt`: string No - - Optional system prompt to steer the model.
- `reasoning_mode`: string No no_think no_think, think Whether the model should emit an explicit reasoning trace.
- `max_tokens`: integer No 1024 - Maximum number of tokens to generate.
- `temperature`: number No 0.7 - Sampling temperature. Lower values are more deterministic.
- `top_p`: number No 0.95 0 ~ 1 Nucleus sampling probability mass.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Nvidia Nemotron 3 Nano Omni Vision

- **Model ID:** `nvidia/nemotron-3-nano-omni/vision`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/nvidia/nemotron-3-nano-omni/vision`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/nvidia/nvidia-nemotron-3-nano-omni-vision

**Request Parameters**

- `prompt`: string Yes - Text prompt to send to the model. English only.
- `image`: string Yes - Image URL to analyze with the model.
- `system_prompt`: string No - - Optional system prompt to steer the model.
- `reasoning_mode`: string No no_think no_think, think Whether the model should emit an explicit reasoning trace.
- `max_tokens`: integer No 1024 - Maximum number of tokens to generate.
- `temperature`: number No 0.7 - Sampling temperature. Lower values are more deterministic.
- `top_p`: number No 0.95 0 ~ 1 Nucleus sampling probability mass.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Openai Dall E 2

- **Model ID:** `openai/dall-e-2`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/openai/dall-e-2`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/openai/openai-dall-e-2

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Openai Dall E 3

- **Model ID:** `openai/dall-e-3`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/openai/dall-e-3`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/openai/openai-dall-e-3

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 1024*1024 1024*1024, 1024*1792, 1792*1024 The size of the generated media in pixels (width*height).
- `quality`: string No standard hd, standard The quality of the generated image.
- `style`: string No vivid vivid, natural The style of the generated image.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Openai Gpt Image 1

- **Model ID:** `openai/gpt-image-1`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/openai/gpt-image-1`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/openai/openai-gpt-image-1

**Request Parameters**

- `image`: string Yes - The image to edit.
- `prompt`: string Yes - The positive prompt for the generation.
- `quality`: string No medium high, medium, low The quality of the generated image.
- `mask_image`: string No - An additional image whose fully transparent areas (e.g. where alpha is zero) indicate where image should be edited. If there are multiple images provided, the mask will be applied on the first image. Must be a valid PNG file
- `size`: string No auto auto, 1024*1024, 1024*1536, 1536*1024 The size of the generated media in pixels (width*height).
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Openai Gpt Image 1 High Fidelity

- **Model ID:** `openai/gpt-image-1-high-fidelity`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/openai/gpt-image-1-high-fidelity`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/openai/openai-gpt-image-1-high-fidelity

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.

### Openai Gpt Image 1 Mini Edit

- **Model ID:** `openai/gpt-image-1-mini/edit`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/openai/gpt-image-1-mini/edit`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/openai/openai-gpt-image-1-mini-edit

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `images`: array No [] 1 ~ 4 items The images to edit.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Openai Gpt Image 1 Mini Text To Image

- **Model ID:** `openai/gpt-image-1-mini/text-to-image`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/openai/gpt-image-1-mini/text-to-image`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/openai/openai-gpt-image-1-mini-text-to-image

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Openai Gpt Image 1.5 Edit

- **Model ID:** `openai/gpt-image-1.5/edit`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/openai/gpt-image-1.5/edit`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/openai/openai-gpt-image-1.5-edit

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `images`: array No [] 1 ~ 10 items The images to edit.
- `size`: string No - 1024*1024, 1024*1536, 1536*1024 The size of the generated media in pixels (width*height).
- `background`: string No opaque auto, transparent, opaque Background for the generated image
- `quality`: string No medium low, medium, high The quality of the generated image.
- `input_fidelity`: string No high low, high input fidelity, which allows you to better preserve details from the input images in the output. This is especially useful when using images that contain elements like faces or logos that require accurate preservati
- `output_format`: string No jpeg jpeg, png The format of the output image.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Openai Gpt Image 1.5 Text To Image

- **Model ID:** `openai/gpt-image-1.5/text-to-image`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/openai/gpt-image-1.5/text-to-image`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/openai/openai-gpt-image-1.5-text-to-image

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 1024*1024 1024*1024, 1024*1536, 1536*1024 The size of the generated media in pixels (width*height).
- `quality`: string No medium low, medium, high The quality of the generated image.
- `background`: string No opaque auto, transparent, opaque Background for the generated image
- `output_format`: string No jpeg jpeg, png The format of the output image.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Openai Gpt Image 1 Text To Image

- **Model ID:** `openai/gpt-image-1/text-to-image`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/openai/gpt-image-1/text-to-image`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/openai/openai-gpt-image-1-text-to-image

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 1024*1024 1024*1024, 1024*1536, 1536*1024 The size of the generated media in pixels (width*height).
- `quality`: string No medium low, medium, high The quality of the generated image.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Openai Gpt Image 2 Edit

- **Model ID:** `openai/gpt-image-2/edit`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/openai/gpt-image-2/edit`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/openai/openai-gpt-image-2-edit

**Request Parameters**

- `images`: array Yes [] - List of URLs of input images for editing.
- `prompt`: string Yes - The positive prompt for the generation.
- `aspect_ratio`: string No - 1:1, 3:2, 2:3, 3:4, 4:3, 4:5, 5:4, 9:16, 16:9, 21:9 The aspect ratio of the generated image. Auto-detected from input image if not specified.
- `resolution`: string No 1k 1k, 2k, 4k The resolution of the output image.
- `quality`: string No medium low, medium, high The quality of the generated image. Higher quality costs more.
- `output_format`: string No png png, jpeg, webp The format of the output image.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Openai Gpt Image 2 Text To Image

- **Model ID:** `openai/gpt-image-2/text-to-image`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/openai/gpt-image-2/text-to-image`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/openai/openai-gpt-image-2-text-to-image

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `aspect_ratio`: string No - 1:1, 3:2, 2:3, 3:4, 4:3, 4:5, 5:4, 9:16, 16:9, 21:9 The aspect ratio of the generated image.
- `resolution`: string No 1k 1k, 2k, 4k The resolution of the output image.
- `quality`: string No medium low, medium, high The quality of the generated image. Higher quality costs more.
- `output_format`: string No png png, jpeg, webp The format of the output image.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Openai Sora

- **Model ID:** `openai/sora`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/openai/sora`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/openai/openai-sora

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 480*480 480*480, 480*854, 854*480, 720*720, 720*1280, 1280*720, 1080*1080, 1080*1920, 1920*1080 The size of the generated media in pixels (width*height).

### Openai Sora 2 Characters

- **Model ID:** `openai/sora-2/characters`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/openai/sora-2/characters`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/openai/openai-sora-2-characters

**Request Parameters**

- `video`: string Yes - URL of an MP4 video (minimum 720p, max ~2.67:1 aspect ratio) to define the character. Videos exceeding 1080p are automatically scaled down. Non-standard aspect ratios are automatically padded to 16:9 (landscape) or 9:16 (portra
- `name`: string Yes - - Name for the character (1–80 characters). Refer to this name in prompts when using the character.

### Pika V2.2 Pikaframes

- **Model ID:** `pika/v2.2-pikaframes`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/pika/v2.2-pikaframes`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/pika/pika-v2.2-pikaframes

**Request Parameters**

- `images`: array Yes [] 2 ~ 5 items URL of ref images to use while generating the video.
- `prompt`: string No - The positive prompt for the generation.
- `transitions`: array No - - Configuration for each transition. Length must be len(image_urls) - 1. Total duration of all transitions must not exceed 25 seconds. If not provided, uses default 5-second transitions with the global prompt
- `resolution`: string No 720p 720p, 1080p The resolution of the generated video.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Pixverse Pixverse V5 Effects

- **Model ID:** `pixverse/pixverse-v5-effects`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/pixverse/pixverse-v5-effects`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/pixverse/pixverse-pixverse-v5-effects

**Request Parameters**

- `image`: string Yes - Supported image formats include.jpg/.jpeg/.png; The image file size cannot exceed 10MB, and the image resolution should not be less than 300*300px, and the aspect ratio of the image should be between 1:2.5 ~ 2.5:1.
- `effect`: string Yes Kiss Baby Arrived, Bald Swipe, Bikini Up, BOOM DROP, Creepy Devil Smile, Dishes Served, Dragon Evoker, Dust Me Away, Earth Zoom Challenge, Eye Zoom Challenge, Fin-tastic Mermaid, Ghostface Terror, Holy Wings, Hug Your Love, Huge 
- `resolution`: string No 540p 360p, 540p, 720p, 1080p Video quality (360p/540p/720p/1080p).
- `duration`: integer No 5 5, 8 Video duration in seconds.
- `sound_effect_switch`: boolean No true - Set to true if you want to enable this feature.
- `negative_prompt`: string No - The negative prompt for the generation.

### Pixverse Pixverse V5.5 Effects

- **Model ID:** `pixverse/pixverse-v5.5-effects`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/pixverse/pixverse-v5.5-effects`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/pixverse/pixverse-pixverse-v5.5-effects

**Request Parameters**

- `image`: string Yes - Supported image formats include.jpg/.jpeg/.png; The image file size cannot exceed 10MB, and the image resolution should not be less than 300*300px, and the aspect ratio of the image should be between 1:2.5 ~ 2.5:1.
- `effect`: string Yes Kiss Me AI 3D Figurine Factor, 3D Naked-Eye AD, Anything, Robot, Baby Arrived, Baby Face, Bald Swipe, Bikini Up, Black Myth: Wukong, BOOM DROP, Creepy Devil Smile, Dishes Served, Dragon Evoker, Dust Me Away, Earth Zoom, Eye Zoom 
- `resolution`: string No 540p 360p, 540p, 720p, 1080p Video quality (360p/540p/720p/1080p).
- `duration`: integer No 5 5, 8, 10 Video duration in seconds.
- `negative_prompt`: string No - The negative prompt for the generation.
- `thinking_type`: string No auto enabled, disabled, auto Prompt reasoning enhancement. Controls whether the system should enhance your prompt with internal reasoning and optimization. "enabled" : Turn on system-level optimization. "disabled" : Turn off syste

### Pixverse Pixverse V6 Extend

- **Model ID:** `pixverse/pixverse-v6/extend`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/pixverse/pixverse-v6/extend`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/pixverse/pixverse-pixverse-v6-extend

**Request Parameters**

- `prompt`: string Yes - Text description of the desired video.
- `video`: string Yes - URL of the input video to extend
- `resolution`: string No 720p 360p, 540p, 720p, 1080p The resolution of the generated video
- `duration`: integer No 5 1 ~ 15 The duration of the generated video in seconds. v6 supports values from 1 to 15 seconds
- `generate_audio_switch`: boolean No false - Enable audio generation for the video.
- `negative_prompt`: string No - The negative prompt for the generation.
- `style`: string No - anime, 3d_animation, clay, comic, cyberpunk The style of the extended video
- `seed`: integer No - -1 ~ 2147483647 Random seed for generation.

### Pixverse Swap

- **Model ID:** `pixverse/swap`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/pixverse/swap`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/pixverse/pixverse-swap

**Request Parameters**

- `image`: string Yes - The model generates video with the picture passed in as the first frame.Base64 encoded strings in data:image/jpeg; base64,{data} format for incoming images, or URLs accessible via the public network. The uploaded image needs to
- `video`: string Yes - The video to be swapped.
- `resolution`: string No 720p 360p, 540p, 720p Video quality (360p/540p/720p).
- `mode`: string No person person, object, background The swap mode to use Default value: person

### Recraft AI Recraft 20b

- **Model ID:** `recraft-ai/recraft-20b`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/recraft-ai/recraft-20b`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/recraft-ai/recraft-ai-recraft-20b

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `aspect_ratio`: string No 1:1 1:1, 16:9, 9:16, 4:3, 3:4 The aspect ratio of the generated media.
- `style`: string No realistic_image/b_and_w realistic_image, realistic_image/b_and_w, realistic_image/enterprise, realistic_image/hard_flash, realistic_image/hdr, realistic_image/motion_blur, realistic_image/natural_light, realistic_image/studio_port
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Recraft AI Recraft 20b Svg

- **Model ID:** `recraft-ai/recraft-20b-svg`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/recraft-ai/recraft-20b-svg`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/recraft-ai/recraft-ai-recraft-20b-svg

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `aspect_ratio`: string No 1:1 1:1, 16:9, 9:16, 4:3, 3:4 The aspect ratio of the generated media.
- `style`: string No vector_illustration vector_illustration, vector_illustration/cartoon, vector_illustration/doodle_line_art, vector_illustration/engraving, vector_illustration/flat_2, vector_illustration/kawaii, vector_illustration/line_art, vector
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Recraft AI Recraft V3

- **Model ID:** `recraft-ai/recraft-v3`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/recraft-ai/recraft-v3`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/recraft-ai/recraft-ai-recraft-v3

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `aspect_ratio`: string No 1:1 1:1, 16:9, 9:16, 4:3, 3:4 The aspect ratio of the generated media.
- `style`: string No - realistic_image, digital_illustration, digital_illustration/pixel_art, digital_illustration/hand_drawn, digital_illustration/grain, digital_illustration/infantile_sketch, digital_illustration/2d_art_poster, digital_illustration/
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Recraft AI Recraft V3 Svg

- **Model ID:** `recraft-ai/recraft-v3-svg`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/recraft-ai/recraft-v3-svg`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/recraft-ai/recraft-ai-recraft-v3-svg

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `aspect_ratio`: string No 1:1 1:1, 16:9, 9:16, 4:3, 3:4 The aspect ratio of the generated media.
- `style`: string No - engraving, line_art, line_circuit, linocut Style of the generated image.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Recraft AI Recraft V4 Pro Text To Image

- **Model ID:** `recraft-ai/recraft-v4-pro/text-to-image`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/recraft-ai/recraft-v4-pro/text-to-image`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/recraft-ai/recraft-ai-recraft-v4-pro-text-to-image

**Request Parameters**

- `prompt`: string Yes - Text description of the image to generate (max 10,000 characters).
- `size`: string No 1024*1024 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).

### Recraft AI Recraft V4 Pro Text To Vector

- **Model ID:** `recraft-ai/recraft-v4-pro/text-to-vector`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/recraft-ai/recraft-v4-pro/text-to-vector`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/recraft-ai/recraft-ai-recraft-v4-pro-text-to-vector

**Request Parameters**

- `prompt`: string Yes - Text description of the vector image to generate (max 10,000 characters).
- `size`: string No 1024*1024 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).

### Recraft AI Recraft V4 Text To Image

- **Model ID:** `recraft-ai/recraft-v4/text-to-image`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/recraft-ai/recraft-v4/text-to-image`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/recraft-ai/recraft-ai-recraft-v4-text-to-image

**Request Parameters**

- `prompt`: string Yes - Text description of the image to generate (max 10,000 characters).
- `size`: string No 1024*1024 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).

### Recraft AI Recraft V4 Text To Vector

- **Model ID:** `recraft-ai/recraft-v4/text-to-vector`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/recraft-ai/recraft-v4/text-to-vector`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/recraft-ai/recraft-ai-recraft-v4-text-to-vector

**Request Parameters**

- `prompt`: string Yes - Text description of the vector image to generate (max 10,000 characters).
- `size`: string No 1024*1024 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).

### Reve Edit

- **Model ID:** `reve/edit`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/reve/edit`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/reve/reve-edit

**Request Parameters**

- `image`: string Yes - The image to edit.
- `prompt`: string Yes - The positive prompt for the generation.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation.

### Reve Edit Fast

- **Model ID:** `reve/edit-fast`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/reve/edit-fast`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/reve/reve-edit-fast

**Request Parameters**

- `image`: string Yes - The image to edit-fast.
- `prompt`: string Yes - The positive prompt for the generation.
- `output_format`: string No jpeg jpeg, png, webp The format of the output image.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Reve Remix

- **Model ID:** `reve/remix`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/reve/remix`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/reve/reve-remix

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `reference_images`: array Yes - 1 ~ 4 items A list of images to use as style references.
- `aspect_ratio`: string No 1:1 21:9, 16:9, 4:3, 1:1, 3:4, 9:16, 9:21 The aspect ratio of the generated media.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation.

### Reve Remix Fast

- **Model ID:** `reve/remix-fast`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/reve/remix-fast`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/reve/reve-remix-fast

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `reference_images`: array Yes - 1 ~ 4 items A list of images to use as style references.
- `aspect_ratio`: string No 1:1 16:9, 9:16, 3:2, 2:3, 4:3, 3:4, 1:1 The aspect ratio of the generated media.
- `output_format`: string No jpeg jpeg, png, webp The format of the output image.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Reve Text To Image

- **Model ID:** `reve/text-to-image`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/reve/text-to-image`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/reve/reve-text-to-image

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `aspect_ratio`: string No 1:1 21:9, 16:9, 4:3, 1:1, 3:4, 9:16, 9:21 The aspect ratio of the generated media.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation.

### Runwayml Gen4 Aleph

- **Model ID:** `runwayml/gen4-aleph`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/runwayml/gen4-aleph`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/runwayml/runwayml-gen4-aleph

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `video`: string Yes - Input video to generate from. Videos must be less than 16MB. Only 5s of the input video will be used.
- `aspect_ratio`: string No 16:9 16:9, 4:3, 1:1, 3:4, 9:16 The aspect ratio of the generated media.
- `reference_image`: string No - - Reference image to influence the style or content of the output

### Runwayml Gen4 Image

- **Model ID:** `runwayml/gen4-image`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/runwayml/gen4-image`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/runwayml/runwayml-gen4-image

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `aspect_ratio`: string No 4:3 1:1, 16:9, 9:16, 4:3, 3:4 The aspect ratio of the generated media.
- `resolution`: string No 1080p 1080p, 720p The resolution of the generated media.
- `reference_images`: array No - - A list of images to use as style references.
- `seed`: integer No - -1 ~ 2147483647 The seed to use for the image generation.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Runwayml Gen4 Image Turbo

- **Model ID:** `runwayml/gen4-image-turbo`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/runwayml/gen4-image-turbo`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/runwayml/runwayml-gen4-image-turbo

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `aspect_ratio`: string No 4:3 1:1, 16:9, 9:16, 4:3, 3:4 The aspect ratio of the generated media.
- `resolution`: string No 1080p 1080p, 720p The resolution of the generated media.
- `reference_images`: array No - 1 ~ 3 items A list of images to use as style references.
- `seed`: integer No - -1 ~ 2147483647 The seed to use for the image generation.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Runwayml Gen4 Turbo

- **Model ID:** `runwayml/gen4-turbo`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/runwayml/gen4-turbo`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/runwayml/runwayml-gen4-turbo

**Request Parameters**

- `image`: string Yes - The image for generating the output.
- `prompt`: string Yes - The positive prompt for the generation.
- `duration`: integer No 5 5 ~ 10 The duration of the generated media in seconds.
- `aspect_ratio`: string No - 16:9, 4:3, 1:1, 3:4, 9:16 The aspect ratio of the generated media.

### Scenario Marketing Aorbit Dolly Fast

- **Model ID:** `scenario-marketing/auto-spin`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/scenario-marketing/auto-spin`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/scenario-marketing/scenario-marketing-aorbit-dolly-fast

**Request Parameters**

- `image`: string Yes - An image to be used for creating auto-spin video. Requirements: 1. Only accepts 1 image; 2. Only supports product images; 3. Better results when the subject is shown in close-up, medium, or long-range shots; 4. Images can be pr

### Scenario Marketing Auto Spin

- **Model ID:** `scenario-marketing/auto-spin`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/scenario-marketing/auto-spin`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/scenario-marketing/scenario-marketing-auto-spin

**Request Parameters**

- `image`: string Yes - An image to be used for creating auto-spin video. Requirements: 1. Only accepts 1 image; 2. Only supports product images; 3. Better results when the subject is shown in close-up, medium, or long-range shots; 4. Images can be pr

### Scenario Marketing Orbit

- **Model ID:** `scenario-marketing/orbit`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/scenario-marketing/orbit`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/scenario-marketing/scenario-marketing-orbit

**Request Parameters**

- `image`: string Yes - An image to be used for creating orbit video. Requirements: 1. Only accepts 1 image; 2. Supports single or multiple product images; 3. Better results when product images are close-up, medium, or long shots with reference object

### Scenario Marketing Spin180

- **Model ID:** `scenario-marketing/spin180`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/scenario-marketing/spin180`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/scenario-marketing/scenario-marketing-spin180

**Request Parameters**

- `image`: string Yes -

### Scenario Marketing Walk Forward

- **Model ID:** `scenario-marketing/walk-forward`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/scenario-marketing/walk-forward`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/scenario-marketing/scenario-marketing-walk-forward

**Request Parameters**

- `image`: string Yes -

### Sourceful Riverflow 2.0 Pro Edit

- **Model ID:** `sourceful/riverflow-2.0-pro/edit`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/sourceful/riverflow-2.0-pro/edit`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/sourceful/sourceful-riverflow-2.0-pro-edit

**Request Parameters**

- `images`: array Yes [] 1 ~ 10 items Source images for editing (1-10 images required).
- `prompt`: string Yes - Text description of the edits you want to make to the source images.
- `resolution`: string No 1k 1k, 2k, 4k Output image resolution. Higher resolution produces more detailed images but costs more.
- `aspect_ratio`: string No auto auto, 21:9, 16:9, 3:2, 4:3, 5:4, 1:1, 4:5, 3:4, 2:3, 9:16 Aspect ratio of the output image. Use 'auto' to preserve source aspect ratio.
- `transparency`: boolean No false - Enable transparent background when supported by the edited content.

### Sourceful Riverflow 2.0 Pro Text To Image

- **Model ID:** `sourceful/riverflow-2.0-pro/text-to-image`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/sourceful/riverflow-2.0-pro/text-to-image`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/sourceful/sourceful-riverflow-2.0-pro-text-to-image

**Request Parameters**

- `prompt`: string Yes - Text description of the image you want to generate.
- `resolution`: string No 1k 1k, 2k, 4k Output image resolution. Higher resolution produces more detailed images but costs more.
- `aspect_ratio`: string No 1:1 1:1, 21:9, 16:9, 3:2, 4:3, 5:4, 4:5, 3:4, 2:3, 9:16 Aspect ratio of the generated image.
- `transparency`: boolean No false - Enable transparent background when supported by the generated content.

### Stability AI Sdxl

- **Model ID:** `stability-ai/sdxl`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/stability-ai/sdxl`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/stability-ai/stability-ai-sdxl

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `image`: string No - The image to generate an image from.
- `size`: string No 1024*1024 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Stability AI Sdxl LoRA

- **Model ID:** `stability-ai/sdxl-lora`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/stability-ai/sdxl-lora`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/stability-ai/stability-ai-sdxl-lora

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `negative_prompt`: string No - The negative prompt for the generation.
- `size`: string No 1024*1024 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `guidance_scale`: number No 3.5 1.0 ~ 20.0 The guidance scale to use for the generation.
- `num_inference_steps`: integer No 28 1 ~ 50 The number of inference steps to perform.
- `loras`: array No max 4 items List of LoRAs to apply (max 4). loras[].path string Yes - Path to the LoRA model loras[].scale float Yes - 0.0 ~ 4.0 Scale of the LoRA model
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Stability AI Stable Diffusion

- **Model ID:** `stability-ai/stable-diffusion`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/stability-ai/stable-diffusion`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/stability-ai/stability-ai-stable-diffusion

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `image`: string No - The image to generate an image from.
- `size`: string No 1024*1024 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Stability AI Stable Diffusion 3

- **Model ID:** `stability-ai/stable-diffusion-3`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/stability-ai/stable-diffusion-3`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/stability-ai/stability-ai-stable-diffusion-3

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `image`: string No - The image to generate an image from.
- `aspect_ratio`: string No 1:1 1:1, 3:4, 4:3, 16:9, 9:16 The aspect ratio of the generated media.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Stability AI Stable Diffusion 3.5 Large

- **Model ID:** `stability-ai/stable-diffusion-3.5-large`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/stability-ai/stable-diffusion-3.5-large`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/stability-ai/stability-ai-stable-diffusion-3.5-large

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `image`: string No - The image to generate an image from.
- `aspect_ratio`: string No 1:1 1:1, 3:4, 4:3, 16:9, 9:16 The aspect ratio of the generated media.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Stability AI Stable Diffusion 3.5 Large Turbo

- **Model ID:** `stability-ai/stable-diffusion-3.5-large-turbo`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/stability-ai/stable-diffusion-3.5-large-turbo`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/stability-ai/stability-ai-stable-diffusion-3.5-large-turbo

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `image`: string No - The image to generate an image from.
- `aspect_ratio`: string No 1:1 1:1, 3:4, 4:3, 16:9, 9:16 The aspect ratio of the generated media.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Stability AI Stable Diffusion 3.5 Medium

- **Model ID:** `stability-ai/stable-diffusion-3.5-medium`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/stability-ai/stable-diffusion-3.5-medium`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/stability-ai/stability-ai-stable-diffusion-3.5-medium

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `image`: string No - The image to generate an image from.
- `aspect_ratio`: string No 1:1 1:1, 3:4, 4:3, 16:9, 9:16 The aspect ratio of the generated media.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Sync React 1

- **Model ID:** `sync/react-1`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/sync/react-1`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/sync/sync-react-1

**Request Parameters**

- `video`: string Yes - Input video file (.mp4)
- `audio`: string Yes - - Input audio file (.wav)
- `emotion`: string No neutral happy, sad, angry, disgusted, surprised, neutral Emotion prompt for the generation (single word emotions only)
- `model_mode`: string No face lips, face, head Edit region for the model (lips/face/head). When head is selected, model generates natural talking head movements along with emotions + lipsync

### Topaz Image Denoise

- **Model ID:** `topaz/image/denoise`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/topaz/image/denoise`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/topaz/topaz-image-denoise

**Request Parameters**

- `image`: string No - The image file to be processed. Supported formats (png jpg jpeg tiff tif)
- `model`: string No Normal Normal, Strong, Extreme The denoise model to use. Normal: Balanced noise reduction. Strong: More aggressive noise removal. Extreme: Maximum noise reduction for heavily degraded images.
- `output_format`: string No jpeg jpeg, jpg, png The format of the output image.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Topaz Image Lighting

- **Model ID:** `topaz/image/lighting`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/topaz/image/lighting`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/topaz/topaz-image-lighting

**Request Parameters**

- `image`: string No - The image file to be processed. Supported formats (png jpg jpeg tiff tif)
- `model`: string No Adjust Adjust, Adjust V2, White Balance, Colorize The lighting model to use. Adjust: Balance exposure and lighting. Adjust V2: Enhanced lighting adjustment. White Balance: Correct color temperature. Colorize: Add natural color to 
- `output_format`: string No jpeg jpeg, jpg, png, tiff, tif The format of the output image.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Topaz Image Restore

- **Model ID:** `topaz/image/restore`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/topaz/image/restore`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/topaz/topaz-image-restore

**Request Parameters**

- `image`: string No - The image file to be processed. Supported formats (png jpg jpeg tiff tif)
- `model`: string No Dust-Scratch Dust-Scratch, Dust-Scratch V2 The restore model to use. Dust-Scratch: Remove dust and scratches from old photos. Dust-Scratch V2: Enhanced dust and scratch removal with better detail preservation.
- `output_format`: string No jpeg jpeg, jpg, png, tiff, tif The format of the output image.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Topaz Image Sharpen

- **Model ID:** `topaz/image/sharpen`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/topaz/image/sharpen`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/topaz/topaz-image-sharpen

**Request Parameters**

- `image`: string No - The image file to be processed. Supported formats (png jpg jpeg tiff tif)
- `model`: string No Standard Standard, Strong, Lens Blur, Lens Blur V2, Motion Blur, Natural, Refocus, Wildlife, Portrait The sharpen model to use. Standard: Balanced sharpening. Strong: Aggressive sharpening. Lens Blur: Fix lens blur. Lens Blur V2: 
- `output_format`: string No jpeg jpeg, jpg, png, tiff, tif The format of the output image.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Veed Fabric 1.0

- **Model ID:** `veed/fabric-1.0`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/veed/fabric-1.0`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/veed/veed-fabric-1.0

**Request Parameters**

- `image`: string Yes - The image for generating the output.
- `audio`: string Yes - - The audio for generating the output.
- `resolution`: string No 480p 480p, 720p The resolution of the output video.

### Vidu One Click V2 Mv

- **Model ID:** `vidu/one-click-v2/mv`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/vidu/one-click-v2/mv`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/vidu/vidu-one-click-v2-mv

**Request Parameters**

- `images`: array Yes [] 1 ~ 7 items The model will use the provided images as references to generate a video with consistent subjects. For fields that accept images: Accepts 1 to 3 images; Images Assets can be provided via URLs or Base64 encode; You m
- `audio`: string Yes - - The music you want to generate MV.The post body of the HTTP request should not exceed 20MB, and it must include an appropriate content type string.
- `prompt`: string No - The positive prompt for the generation.
- `aspect_ratio`: string No 16:9 16:9, 9:16, 1:1, 4:3, 3:4 The aspect ratio of the generated media.
- `resolution`: string No 720p 540p, 720p, 1080p The resolution of the generated media.
- `add_subtitle`: boolean No false - need subtitle

### Vidu Reference To Image Q2

- **Model ID:** `vidu/reference-to-image-q2`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/vidu/reference-to-image-q2`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/vidu/vidu-reference-to-image-q2

**Request Parameters**

- `images`: array Yes [] 1 ~ 7 items The reference image to guide the generation.
- `prompt`: string Yes - The text prompt for generating the image.
- `aspect_ratio`: string No auto auto, 1:1, 16:9, 9:16, 4:3, 3:4, 21:9, 2:3, 3:2 The aspect ratio for the generated image. 'auto' Generated image aspect ratio is consistent with the first input images.
- `resolution`: string No 1080p 1080p, 2K, 4K The output resolution quality: 1080p (1920x1080), 2K (2560x1440), or 4K (3840x2160).
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Vidu Template Halloween

- **Model ID:** `vidu/template/halloween`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/vidu/template/halloween`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/vidu/vidu-template-halloween

**Request Parameters**

- `image`: string Yes - The reference images for generating the output.
- `template`: string Yes tim_burton tim_burton, broomstick_fly, witchy_pet, pumpkin_head, sexy_devil, dance_with_ghost, crow_arrival, clown_makeup, shadow_of_terror_video, not_look_back_video, turn_into_zombie, head_to_balloon, covered_liquid_metal, wedn
- `bgm`: boolean No true - The background music for generating the output.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Vidu Text To Image Q2

- **Model ID:** `vidu/text-to-image-q2`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/vidu/text-to-image-q2`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/vidu/vidu-text-to-image-q2

**Request Parameters**

- `prompt`: string Yes - The text prompt for generating the image.
- `aspect_ratio`: string No 1:1 1:1, 16:9, 9:16, 4:3, 3:4, 21:9, 2:3, 3:2 The aspect ratio for the generated image.
- `resolution`: string No 1080p 1080p, 2K, 4K The output resolution quality: 1080p (1920x1080), 2K (2560x1440), or 4K (3840x2160).
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### SkyReels V1

- **Model ID:** `wavespeed-ai/SkyReels-V1`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/SkyReels-V1`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/SkyReels-V1

**Request Parameters**

- `image`: string Yes - URL of the image input.
- `prompt`: string Yes - The positive prompt for the generation.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation.

### AI Age Filter

- **Model ID:** `wavespeed-ai/ai-age-filter`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/ai-age-filter`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/ai-age-filter

**Request Parameters**

- `image`: string Yes - The URL of the input image.
- `age`: string Yes old baby, child, teen, young_adult, middle_aged, old, very_old Target age: baby (2), child (8), teen (15), young adult (22), middle aged (45), old (75), very old (90).

### AI Breast Expansion

- **Model ID:** `wavespeed-ai/ai-breast-expansion`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/ai-breast-expansion`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/ai-breast-expansion

**Request Parameters**

- `image`: string Yes - The URL of the input image.

### AI Celebrity Look Alike Finder

- **Model ID:** `wavespeed-ai/ai-celebrity-look-alike-finder`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/ai-celebrity-look-alike-finder`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/ai-celebrity-look-alike-finder

**Request Parameters**

- `image`: string Yes - The URL of the input image.

### AI Dog Selfie

- **Model ID:** `wavespeed-ai/ai-dog-selfie`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/ai-dog-selfie`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/ai-dog-selfie

**Request Parameters**

- `image`: string Yes - The URL of the input image (optional).
- `breed`: string No random random, golden_retriever, husky, corgi, poodle, labrador, shiba, pomeranian, bulldog, dalmatian, samoyed Dog breed. Choose from presets or enter a custom breed.
- `count`: integer No 1 1 ~ 5 Number of images to generate (1-5).
- `dog_size`: string No any any, puppy, adult Dog age: puppy or adult.
- `style`: string No casual casual, studio, outdoor, christmas, beach, cozy Photo style.
- `expression`: string No happy happy, silly, cool, sleeping Dog expression.

### AI Fat Filter

- **Model ID:** `wavespeed-ai/ai-fat-filter`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/ai-fat-filter`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/ai-fat-filter

**Request Parameters**

- `image`: string Yes - The URL of the input image.

### AI Fortune Teller

- **Model ID:** `wavespeed-ai/ai-fortune-teller`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/ai-fortune-teller`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/ai-fortune-teller

**Request Parameters**

- `name`: string Yes - - Your name.
- `birthday`: string Yes - - Your birthday with time for more accuracy (e.g. 1990-01-15 08:30).
- `gender`: string Yes - male, female Your gender.
- `birthplace`: string No - - Your birthplace (optional).
- `current_location`: string No - - Your current location (optional).
- `is_married`: boolean No false - Whether you are married.
- `question`: string No - - Specific question about career, love, health, etc. (optional).
- `image`: string No - Palm or face photo for vision-based reading (optional).

### AI Gender Swap

- **Model ID:** `wavespeed-ai/ai-gender-swap`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/ai-gender-swap`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/ai-gender-swap

**Request Parameters**

- `image`: string Yes - The URL of the input image.

### AI Girl Filter

- **Model ID:** `wavespeed-ai/ai-girl-filter`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/ai-girl-filter`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/ai-girl-filter

**Request Parameters**

- `image`: string Yes - The URL of the input image.

### AI Instagram Model

- **Model ID:** `wavespeed-ai/ai-instagram-model`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/ai-instagram-model`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/ai-instagram-model

**Request Parameters**

- `image`: string Yes - The URL of the input image.
- `prompt`: string No - Text prompt describing the desired Instagram-style photo.
- `style`: string No influencer influencer, street_fashion, beach, fitness, luxury, casual_chic, night_glam, anime, cyberpunk, vintage_retro Style preset for the generated photo.

### AI Kissing

- **Model ID:** `wavespeed-ai/ai-kissing`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/ai-kissing`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/ai-kissing

**Request Parameters**

- `image`: string Yes - The URL of the first person's image.
- `right_image`: string No - - The URL of the second person's image. If provided, both people will be composited into a single frame before video generation.

### AI Math Solver

- **Model ID:** `wavespeed-ai/ai-math-solver`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/ai-math-solver`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/ai-math-solver

**Request Parameters**

- `image`: string Yes - The URL of the input image.

### AI Photo Colorizer

- **Model ID:** `wavespeed-ai/ai-photo-colorizer`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/ai-photo-colorizer`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/ai-photo-colorizer

**Request Parameters**

- `image`: string Yes - The URL of the input image.

### AI Smile Filter

- **Model ID:** `wavespeed-ai/ai-smile-filter`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/ai-smile-filter`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/ai-smile-filter

**Request Parameters**

- `image`: string Yes - The URL of the input image.

### AI Story Generator

- **Model ID:** `wavespeed-ai/ai-story-generator`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/ai-story-generator`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/ai-story-generator

**Request Parameters**

- `prompt`: string Yes - Story theme or idea.
- `genre`: string No auto auto, fantasy, romance, mystery, sci-fi, thriller, horror, comedy, drama, adventure Story genre.
- `length`: string No medium short, medium, long Story length: short (~500 words), medium (~1500 words), long (~3000 words).
- `narrative_perspective`: string No auto auto, first person, second person, third person Narrative perspective.
- `audience`: string No auto auto, children, teens, adults Target audience.
- `format`: string No auto auto, short story, fairy tale, fable, script, poem, letter Story format.

### AI Talking Photos

- **Model ID:** `wavespeed-ai/ai-talking-photos`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/ai-talking-photos`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/ai-talking-photos

**Request Parameters**

- `image`: string Yes - The URL of the input image.
- `text`: string Yes - - The text for the photo to speak.
- `duration`: integer No 5 5 ~ 15 The duration of the generated video in seconds.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### AI Travel Trends

- **Model ID:** `wavespeed-ai/ai-travel-trends`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/ai-travel-trends`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/ai-travel-trends

**Request Parameters**

- `image`: string Yes - The URL of the input image.
- `prompt`: string No - Text prompt describing the desired travel photo.
- `destination`: string No auto auto, paris, santorini, swiss_alps, iceland, rome, london, amalfi, barcelona, norway_fjords, tokyo, kyoto, bali, maldives, dubai, great_wall, taj_mahal, angkor_wat, seoul, new_york, machu_picchu, havana, grand_canyon, rio, sa

### AI Twerk

- **Model ID:** `wavespeed-ai/ai-twerk`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/ai-twerk`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/ai-twerk

**Request Parameters**

- `image`: string Yes - The URL of the person's image to animate.

### Any Llm

- **Model ID:** `wavespeed-ai/any-llm`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/any-llm`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/any-llm

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `system_prompt`: string No - - System prompt to provide context or instructions to the model
- `reasoning`: boolean No false - Should reasoning be the part of the final answer.
- `priority`: string No latency throughput, latency Throughput is the default and is recommended for most use cases. Latency is recommended for use cases where low latency is important.
- `temperature`: number No - 0 ~ 2 This setting influences the variety in the model’s responses. Lower values lead to more predictable and typical responses, while higher values encourage more diverse and less common responses. At 0, the model always gives 
- `max_tokens`: integer No - - This sets the upper limit for the number of tokens the model can generate in response. It won’t produce more than this limit. The maximum value is the context length minus the prompt length.
- `model`: string No google/gemini-2.5-flash anthropic/claude-3.7-sonnet, anthropic/claude-3.5-sonnet, anthropic/claude-3-haiku, google/gemini-2.5-flash, google/gemini-2.0-flash-001, google/gemini-2.0-flash-lite-001, google/gemini-2.5-pro, google/gemi
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Any Llm Vision

- **Model ID:** `wavespeed-ai/any-llm/vision`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/any-llm/vision`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/any-llm-vision

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `system_prompt`: string No - - System prompt to provide context or instructions to the model
- `images`: array No [] - List of image URLs to be processed
- `reasoning`: boolean No false - Should reasoning be the part of the final answer.
- `priority`: string No latency throughput, latency Throughput is the default and is recommended for most use cases. Latency is recommended for use cases where low latency is important.
- `temperature`: number No - 0 ~ 2 This setting influences the variety in the model’s responses. Lower values lead to more predictable and typical responses, while higher values encourage more diverse and less common responses. At 0, the model always gives 
- `max_tokens`: integer No - - This sets the upper limit for the number of tokens the model can generate in response. It won’t produce more than this limit. The maximum value is the context length minus the prompt length.
- `model`: string No google/gemini-2.5-flash google/gemini-3-flash-preview, anthropic/claude-3.7-sonnet, anthropic/claude-3.5-sonnet, anthropic/claude-3-haiku, google/gemini-2.5-flash, google/gemini-2.0-flash-001, google/gemini-2.0-flash-lite-001, goo
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Bitdance 14b Text To Image

- **Model ID:** `wavespeed-ai/bitdance-14b/text-to-image`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/bitdance-14b/text-to-image`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/bitdance-14b-text-to-image

**Request Parameters**

- `prompt`: string Yes - Text description of the image you want to generate
- `size`: string No 1024*1024 256 ~ 1536 per dimension Image resolution in width*height format (e.g., '1024*1024', '512*512')
- `seed`: integer No -1 -1 ~ 2147483647 Random seed for reproducible generation

### Chroma

- **Model ID:** `wavespeed-ai/chroma`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/chroma`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/chroma

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 1024*1024 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `output_format`: string No jpeg jpeg, png, webp The format of the output image.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Content Moderator Image

- **Model ID:** `wavespeed-ai/content-moderator/image`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/content-moderator/image`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/content-moderator-image

**Request Parameters**

- `image`: string No - Image to be moderated.
- `text`: string No - - Text to be moderated.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Content Moderator Text

- **Model ID:** `wavespeed-ai/content-moderator/text`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/content-moderator/text`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/content-moderator-text

**Request Parameters**

- `text`: string Yes - - Text to be moderated.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Emu 3.5 Image Text To Image

- **Model ID:** `wavespeed-ai/emu-3.5-image/text-to-image`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/emu-3.5-image/text-to-image`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/emu-3.5-image-text-to-image

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 640x640 256 ~ 1536 per dimension The size of the output image in WIDTHxHEIGHT format (e.g., '1024x768'). The system will find the closest supported resolution and aspect ratio, then resize to match.
- `seed`: integer No -1 -1 ~ 2147483647 The seed for the inference.
- `output_format`: string No jpeg jpeg, png, webp The format of the output image.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Ernie Image Text To Image

- **Model ID:** `wavespeed-ai/ernie-image/text-to-image`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/ernie-image/text-to-image`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/ernie-image-text-to-image

**Request Parameters**

- `prompt`: string Yes - Text prompt describing the image. Supports English, Chinese, and Japanese.
- `size`: string No 1024*1024 256 ~ 1536 per dimension The size of the generated image in pixels (width*height).

### Ernie Image Text To Image Turbo

- **Model ID:** `wavespeed-ai/ernie-image/text-to-image-turbo`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/ernie-image/text-to-image-turbo`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/ernie-image-text-to-image-turbo

**Request Parameters**

- `prompt`: string Yes - Text prompt describing the image. Supports English, Chinese, and Japanese.
- `size`: string No 1024*1024 256 ~ 1536 per dimension The size of the generated image in pixels (width*height).

### Female Human

- **Model ID:** `wavespeed-ai/female-human`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/female-human`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/female-human

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 1024*1024 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `output_format`: string No jpeg jpeg, png, webp The format of the output image.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Firered Image V1.1 Edit

- **Model ID:** `wavespeed-ai/firered-image-v1.1/edit`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/firered-image-v1.1/edit`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/firered-image-v1.1-edit

**Request Parameters**

- `prompt`: string Yes - The editing instruction describing what changes to make to the image. Supports both English and Chinese.
- `images`: array Yes [] 1 ~ 3 items List of URLs of input images for editing. The maximum number of images is 3.
- `size`: string No - 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).

### Firered Image Edit

- **Model ID:** `wavespeed-ai/firered-image/edit`
- **Operation:** `edit_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/firered-image/edit`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/firered-image-edit

**Request Parameters**

- `images`: array Yes [] 1 ~ 3 items List of URLs of input images for editing. The maximum number of images is 3.
- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No - 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).

### Flashvsr

- **Model ID:** `wavespeed-ai/flashvsr`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/flashvsr`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/flashvsr

**Request Parameters**

- `video`: string Yes - The video to upscale.
- `target_resolution`: string No 1080p 720p, 1080p, 2k, 4k Target resolution to upscale to.

### Flux 1 Srpo

- **Model ID:** `wavespeed-ai/flux-1-srpo`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/flux-1-srpo`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/flux-1-srpo

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 1024*1024 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `strength`: number No 0.8 0.00 ~ 1.00 Strength indicates extent to transform the reference image.
- `num_inference_steps`: integer No 28 1 ~ 50 The number of inference steps to perform.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `guidance_scale`: number No 3.5 1.0 ~ 20.0 The guidance scale to use for the generation.
- `output_format`: string No jpeg jpeg, png The format of the output image.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Flux 1 Srpo Image To Image

- **Model ID:** `wavespeed-ai/flux-1-srpo/image-to-image`
- **Operation:** `edit_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/flux-1-srpo/image-to-image`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/flux-1-srpo-image-to-image

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `image`: string No - The image to generate an image from.
- `strength`: number No 0.8 0.00 ~ 1.00 Strength indicates extent to transform the reference image.
- `num_inference_steps`: integer No 28 1 ~ 50 The number of inference steps to perform.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `guidance_scale`: number No 3.5 1.0 ~ 20.0 The guidance scale to use for the generation.
- `output_format`: string No jpeg jpeg, png The format of the output image.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Flux 1.1 Pro

- **Model ID:** `wavespeed-ai/flux-1.1-pro`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/flux-1.1-pro`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/flux-1.1-pro

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `aspect_ratio`: string No 1:1 1:1, 16:9, 9:16, 4:3, 3:4
- `output_format`: string No jpg jpg, png The format of the output image.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Flux 1.1 Pro Ultra

- **Model ID:** `wavespeed-ai/flux-1.1-pro-ultra`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/flux-1.1-pro-ultra`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/flux-1.1-pro-ultra

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `aspect_ratio`: string No 1:1 1:1, 16:9, 9:16, 4:3, 3:4
- `output_format`: string No jpg jpg, png The format of the output image.
- `raw`: boolean No false - Generate less processed, more natural-looking images.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Flux 2 Dev Edit

- **Model ID:** `wavespeed-ai/flux-2-dev/edit`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/flux-2-dev/edit`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/flux-2-dev-edit

**Request Parameters**

- `images`: array Yes [] 1 ~ 3 items List of URLs of input images for editing. The maximum number of images is 3.
- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No - 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Flux 2 Dev Edit LoRA

- **Model ID:** `wavespeed-ai/flux-2-dev/edit-lora`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/flux-2-dev/edit-lora`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/flux-2-dev-edit-lora

**Request Parameters**

- `images`: array Yes [] 1 ~ 3 items List of URLs of input images for editing. The maximum number of images is 3.
- `prompt`: string Yes - The prompt describing the desired edits to the image.
- `loras`: array No max 3 items List of LoRAs to apply (max 3). loras[].path string Yes - Path to the LoRA model loras[].scale float Yes - 0.0 ~ 4.0 Scale of the LoRA model
- `size`: string No - 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Flux 2 Dev Text To Image

- **Model ID:** `wavespeed-ai/flux-2-dev/text-to-image`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/flux-2-dev/text-to-image`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/flux-2-dev-text-to-image

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 1024*1024 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Flux 2 Dev Text To Image LoRA

- **Model ID:** `wavespeed-ai/flux-2-dev/text-to-image-lora`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/flux-2-dev/text-to-image-lora`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/flux-2-dev-text-to-image-lora

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 1024*1024 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `loras`: array No max 3 items List of LoRAs to apply (max 3). loras[].path string Yes - Path to the LoRA model loras[].scale float Yes - 0.0 ~ 4.0 Scale of the LoRA model
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Flux 2 Flash Edit

- **Model ID:** `wavespeed-ai/flux-2-flash/edit`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/flux-2-flash/edit`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/flux-2-flash-edit

**Request Parameters**

- `images`: array Yes [] 1 ~ 4 items List of URLs of input images for editing. The maximum number of images is 4.
- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No - 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Flux 2 Flash Text To Image

- **Model ID:** `wavespeed-ai/flux-2-flash/text-to-image`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/flux-2-flash/text-to-image`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/flux-2-flash-text-to-image

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 1024*1024 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Flux 2 Flex Edit

- **Model ID:** `wavespeed-ai/flux-2-flex/edit`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/flux-2-flex/edit`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/flux-2-flex-edit

**Request Parameters**

- `images`: array Yes [] 1 ~ 3 items List of URLs of input images for editing. The maximum number of images is 3.
- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No - 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Flux 2 Flex Text To Image

- **Model ID:** `wavespeed-ai/flux-2-flex/text-to-image`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/flux-2-flex/text-to-image`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/flux-2-flex-text-to-image

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 1024*1024 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Flux 2 Klein 4b Edit

- **Model ID:** `wavespeed-ai/flux-2-klein-4b/edit`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/flux-2-klein-4b/edit`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/flux-2-klein-4b-edit

**Request Parameters**

- `images`: array Yes [] 1 ~ 3 items List of reference image URLs (1-3 images).
- `prompt`: string Yes -
- `size`: string No - 256 ~ 1536 per dimension
- `seed`: integer No -1 -1 ~ 2147483647
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Flux 2 Klein 4b Edit LoRA

- **Model ID:** `wavespeed-ai/flux-2-klein-4b/edit-lora`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/flux-2-klein-4b/edit-lora`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/flux-2-klein-4b-edit-lora

**Request Parameters**

- `images`: array Yes [] 1 ~ 3 items List of reference image URLs (1-3 images).
- `prompt`: string Yes - The editing instruction.
- `loras`: array No max 3 items List of LoRAs to apply (maximum 3). loras[].path string Yes - Path to the LoRA model loras[].scale float Yes - 0.0 ~ 4.0 Scale of the LoRA model
- `size`: string No - 256 ~ 1536 per dimension
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Flux 2 Klein 4b Text To Image

- **Model ID:** `wavespeed-ai/flux-2-klein-4b/text-to-image`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/flux-2-klein-4b/text-to-image`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/flux-2-klein-4b-text-to-image

**Request Parameters**

- `prompt`: string Yes -
- `size`: string No 1024*1024 256 ~ 1536 per dimension
- `seed`: integer No -1 -1 ~ 2147483647
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Flux 2 Klein 4b Text To Image LoRA

- **Model ID:** `wavespeed-ai/flux-2-klein-4b/text-to-image-lora`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/flux-2-klein-4b/text-to-image-lora`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/flux-2-klein-4b-text-to-image-lora

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 1024*1024 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `loras`: array No max 3 items List of LoRAs to apply (maximum 3). loras[].path string Yes - Path to the LoRA model loras[].scale float Yes - 0.0 ~ 4.0 Scale of the LoRA model
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Flux 2 Klein 9b Edit

- **Model ID:** `wavespeed-ai/flux-2-klein-9b/edit`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/flux-2-klein-9b/edit`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/flux-2-klein-9b-edit

**Request Parameters**

- `images`: array Yes [] 1 ~ 3 items List of reference image URLs (1-3 images).
- `prompt`: string Yes -
- `size`: string No - 256 ~ 1536 per dimension
- `seed`: integer No -1 -1 ~ 2147483647
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Flux 2 Klein 9b Edit LoRA

- **Model ID:** `wavespeed-ai/flux-2-klein-9b/edit-lora`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/flux-2-klein-9b/edit-lora`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/flux-2-klein-9b-edit-lora

**Request Parameters**

- `images`: array Yes [] 1 ~ 3 items List of reference image URLs (1-3 images).
- `prompt`: string Yes - The editing instruction.
- `loras`: array No max 3 items List of LoRAs to apply (max 3). loras[].path string Yes - Path to the LoRA model loras[].scale float Yes - 0.0 ~ 4.0 Scale of the LoRA model
- `size`: string No - 256 ~ 1536 per dimension
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Flux 2 Klein 9b Text To Image

- **Model ID:** `wavespeed-ai/flux-2-klein-9b/text-to-image`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/flux-2-klein-9b/text-to-image`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/flux-2-klein-9b-text-to-image

**Request Parameters**

- `prompt`: string Yes -
- `size`: string No 1024*1024 256 ~ 1536 per dimension
- `seed`: integer No -1 -1 ~ 2147483647
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Flux 2 Klein 9b Text To Image LoRA

- **Model ID:** `wavespeed-ai/flux-2-klein-9b/text-to-image-lora`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/flux-2-klein-9b/text-to-image-lora`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/flux-2-klein-9b-text-to-image-lora

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 1024*1024 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `loras`: array No max 3 items List of LoRAs to apply (maximum 3). loras[].path string Yes - Path to the LoRA model loras[].scale float Yes - 0.0 ~ 4.0 Scale of the LoRA model
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Flux 2 Max Edit

- **Model ID:** `wavespeed-ai/flux-2-max/edit`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/flux-2-max/edit`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/flux-2-max-edit

**Request Parameters**

- `images`: array Yes [] 1 ~ 3 items List of URLs of input images for editing. The maximum number of images is 3.
- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No - 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Flux 2 Max Text To Image

- **Model ID:** `wavespeed-ai/flux-2-max/text-to-image`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/flux-2-max/text-to-image`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/flux-2-max-text-to-image

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 1024*1024 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Flux 2 Pro Edit

- **Model ID:** `wavespeed-ai/flux-2-pro/edit`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/flux-2-pro/edit`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/flux-2-pro-edit

**Request Parameters**

- `images`: array Yes [] 1 ~ 3 items List of URLs of input images for editing. The maximum number of images is 3.
- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No - 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Flux 2 Pro Text To Image

- **Model ID:** `wavespeed-ai/flux-2-pro/text-to-image`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/flux-2-pro/text-to-image`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/flux-2-pro-text-to-image

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 1024*1024 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Flux 2 Turbo Edit

- **Model ID:** `wavespeed-ai/flux-2-turbo/edit`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/flux-2-turbo/edit`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/flux-2-turbo-edit

**Request Parameters**

- `images`: array Yes [] 1 ~ 4 items List of URLs of input images for editing. The maximum number of images is 4.
- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No - 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Flux 2 Turbo Text To Image

- **Model ID:** `wavespeed-ai/flux-2-turbo/text-to-image`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/flux-2-turbo/text-to-image`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/flux-2-turbo-text-to-image

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 1024*1024 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Flux Controlnet Union Pro 2.0

- **Model ID:** `wavespeed-ai/flux-controlnet-union-pro-2.0`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/flux-controlnet-union-pro-2.0`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/flux-controlnet-union-pro-2.0

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `control_image`: string Yes - - The URL of the control image for ControlNet guidance.
- `size`: string No 1024*1024 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `num_inference_steps`: integer No 28 1 ~ 50 The number of inference steps to perform.
- `guidance_scale`: number No 3.5 0.00 ~ 20.00 The guidance scale to use for the generation.
- `controlnet_conditioning_scale`: number No 0.7 0.00 ~ 2.00 The conditioning scale for ControlNet. Higher values make the output follow the control image more closely.
- `control_guidance_start`: number No - 0.00 ~ 1.00 The fraction of total steps at which ControlNet guidance start.
- `control_guidance_end`: number No 0.8 0.00 ~ 1.00 The fraction of total steps at which ControlNet guidance ends.
- `loras`: array No max 3 items List of LoRAs to apply (max 3). loras[].path string Yes - Path to the LoRA model loras[].scale float Yes - 0.0 ~ 4.0 Scale of the LoRA model
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation.
- `num_images`: integer No 1 1 ~ 4 The number of images to generate.
- `output_format`: string No jpeg jpeg, png, webp The format of the output image.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Flux Dev

- **Model ID:** `wavespeed-ai/flux-dev`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/flux-dev`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/flux-dev

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `image`: string No - The image to generate an image from.
- `mask_image`: string No - The mask image tells the model where to generate new pixels (white) and where to preserve the original image (black). It acts as a stencil or guide for targeted image editing.
- `strength`: number No 0.8 0.00 ~ 1.00 Strength indicates extent to transform the reference image.
- `size`: string No 1024*1024 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `num_inference_steps`: integer No 28 1 ~ 50 The number of inference steps to perform.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `guidance_scale`: number No 3.5 1.0 ~ 20.0 The guidance scale to use for the generation.
- `num_images`: integer No 1 1 ~ 4 The number of images to generate.
- `output_format`: string No jpeg jpeg, png, webp The format of the output image.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Flux Dev LoRA

- **Model ID:** `wavespeed-ai/flux-dev-lora`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/flux-dev-lora`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/flux-dev-lora

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `image`: string No -
- `mask_image`: string No - The mask image tells the model where to generate new pixels (white) and where to preserve the original image (black). It acts as a stencil or guide for targeted image editing.
- `strength`: number No 0.8 0.00 ~ 1.00 Strength indicates extent to transform the reference image.
- `loras`: array No max 4 items List of LoRAs to apply (max 4). loras[].path string Yes - Path to the LoRA model loras[].scale float Yes - 0.0 ~ 4.0 Scale of the LoRA model
- `size`: string No 1024*1024 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `num_inference_steps`: integer No 28 1 ~ 50 The number of inference steps to perform.
- `guidance_scale`: number No 3.5 0.0 ~ 20.0 The guidance scale to use for the generation.
- `num_images`: integer No 1 1 ~ 4 The number of images to generate.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `output_format`: string No jpeg jpeg, png, webp The format of the output image.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Flux Dev LoRA Ultra Fast

- **Model ID:** `wavespeed-ai/flux-dev-lora-ultra-fast`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/flux-dev-lora-ultra-fast`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/flux-dev-lora-ultra-fast

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `image`: string No -
- `mask_image`: string No - The mask image tells the model where to generate new pixels (white) and where to preserve the original image (black). It acts as a stencil or guide for targeted image editing.
- `strength`: number No 0.8 0.01 ~ 1.00 Strength indicates extent to transform the reference image.
- `loras`: array No max 3 items List of LoRAs to apply (max 3). loras[].path string Yes - Path to the LoRA model loras[].scale float Yes - 0.0 ~ 4.0 Scale of the LoRA model
- `size`: string No 1024*1024 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `num_inference_steps`: integer No 28 1 ~ 50 The number of inference steps to perform.
- `guidance_scale`: number No 3.5 0.0 ~ 20.0 The guidance scale to use for the generation.
- `num_images`: integer No 1 1 ~ 4 The number of images to generate.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `output_format`: string No jpeg jpeg, png, webp The format of the output image.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Flux Dev Ultra Fast

- **Model ID:** `wavespeed-ai/flux-dev-ultra-fast`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/flux-dev-ultra-fast`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/flux-dev-ultra-fast

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `image`: string No - The image to generate an image from.
- `mask_image`: string No - The mask image tells the model where to generate new pixels (white) and where to preserve the original image (black). It acts as a stencil or guide for targeted image editing.
- `strength`: number No 0.8 0.0 ~ 1.0 Strength indicates extent to transform the reference image.
- `size`: string No 1024*1024 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `num_inference_steps`: integer No 28 1 ~ 50 The number of inference steps to perform.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `guidance_scale`: number No 3.5 0 ~ 20 The guidance scale to use for the generation.
- `num_images`: integer No 1 1 ~ 4 The number of images to generate.
- `output_format`: string No jpeg jpeg, png, webp The format of the output image.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Flux Fill Dev

- **Model ID:** `wavespeed-ai/flux-fill-dev`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/flux-fill-dev`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/flux-fill-dev

**Request Parameters**

- `image`: string Yes - The URL of the image to generate an image from.
- `mask_image`: string Yes - The URL of the mask image to generate an image from.
- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 1024*1024 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `num_inference_steps`: integer No 28 1 ~ 50 The number of inference steps to perform.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation.
- `guidance_scale`: number No 30 28 ~ 35 The guidance scale to use for the generation.
- `num_images`: integer No 1 1 ~ 4 The number of images to generate.
- `loras`: array No max 3 items List of LoRAs to apply (max 3). loras[].path string Yes - Path to the LoRA model loras[].scale float Yes - 0.0 ~ 4.0 Scale of the LoRA model

### Flux Kontext Dev

- **Model ID:** `wavespeed-ai/flux-kontext-dev`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/flux-kontext-dev`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/flux-kontext-dev

**Request Parameters**

- `prompt`: string No - The positive prompt for the generation.
- `image`: string No - The image to generate an image from.
- `size`: string No - 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `num_inference_steps`: integer No 28 1 ~ 50 The number of inference steps to perform.
- `guidance_scale`: number No 2.5 1.0 ~ 20.0 The guidance scale to use for the generation.
- `num_images`: integer No 1 1 ~ 4 The number of images to generate.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `output_format`: string No jpeg jpeg, png, webp The format of the output image.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Flux Kontext Dev LoRA

- **Model ID:** `wavespeed-ai/flux-kontext-dev-lora`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/flux-kontext-dev-lora`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/flux-kontext-dev-lora

**Request Parameters**

- `prompt`: string No - The positive prompt for the generation.
- `image`: string No - The image to generate an image from.
- `size`: string No - 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `num_inference_steps`: integer No 28 1 ~ 50 The number of inference steps to perform.
- `guidance_scale`: number No 2.5 0.0 ~ 20.0 The guidance scale to use for the generation.
- `num_images`: integer No 1 1 ~ 4 The number of images to generate.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `loras`: array No max 3 items List of LoRAs to apply (max 3). loras[].path string Yes - Path to the LoRA model loras[].scale float Yes - 0.0 ~ 4.0 Scale of the LoRA model
- `output_format`: string No jpeg jpeg, png, webp The format of the output image.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Flux Kontext Dev LoRA Ultra Fast

- **Model ID:** `wavespeed-ai/flux-kontext-dev-lora-ultra-fast`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/flux-kontext-dev-lora-ultra-fast`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/flux-kontext-dev-lora-ultra-fast

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `image`: string No - The image to generate an image from.
- `size`: string No - 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `num_inference_steps`: integer No 28 1 ~ 50 The number of inference steps to perform.
- `guidance_scale`: number No 2.5 0.0 ~ 20.0 The guidance scale to use for the generation.
- `num_images`: integer No 1 1 ~ 4 The number of images to generate.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `loras`: array No max 3 items List of LoRAs to apply (max 3). loras[].path string Yes - Path to the LoRA model loras[].scale float Yes - 0.0 ~ 4.0 Scale of the LoRA model
- `output_format`: string No jpeg jpeg, png, webp The format of the output image.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Flux Kontext Dev Ultra Fast

- **Model ID:** `wavespeed-ai/flux-kontext-dev-ultra-fast`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/flux-kontext-dev-ultra-fast`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/flux-kontext-dev-ultra-fast

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `image`: string No - The image to generate an image from.
- `size`: string No - 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `num_inference_steps`: integer No 28 1 ~ 50 The number of inference steps to perform.
- `guidance_scale`: number No 2.5 0.0 ~ 20.0 The guidance scale to use for the generation.
- `num_images`: integer No 1 1 ~ 4 The number of images to generate.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `output_format`: string No jpeg jpeg, png, webp The format of the output image.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Flux Kontext Dev Multi

- **Model ID:** `wavespeed-ai/flux-kontext-dev/multi`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/flux-kontext-dev/multi`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/flux-kontext-dev-multi

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `images`: array No [] - URL of images to use while generating the image.
- `size`: string No - 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `num_inference_steps`: integer No 28 1 ~ 50 The number of inference steps to perform.
- `guidance_scale`: number No 2.5 1.0 ~ 20.0 The guidance scale to use for the generation.
- `num_images`: integer No 1 1 ~ 4 The number of images to generate.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `output_format`: string No jpeg jpeg, png, webp The format of the output image.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Flux Kontext Dev Multi Ultra Fast

- **Model ID:** `wavespeed-ai/flux-kontext-dev/multi-ultra-fast`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/flux-kontext-dev/multi-ultra-fast`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/flux-kontext-dev-multi-ultra-fast

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `images`: array No [] - URL of images to use while generating the image.
- `size`: string No - 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `num_inference_steps`: integer No 28 1 ~ 50 The number of inference steps to perform.
- `guidance_scale`: number No 2.5 1.0 ~ 20.0 The guidance scale to use for the generation.
- `num_images`: integer No 1 1 ~ 4 The number of images to generate.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `output_format`: string No jpeg jpeg, png, webp The format of the output image.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Flux Kontext Max

- **Model ID:** `wavespeed-ai/flux-kontext-max`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/flux-kontext-max`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/flux-kontext-max

**Request Parameters**

- `image`: string Yes - The image to generate an image from.
- `prompt`: string Yes - The positive prompt for the generation.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation.
- `guidance_scale`: number No 3.5 1.0 ~ 20.0 The guidance scale to use for the generation. aspect_ratio No - 21:9, 16:9, 4:3, 3:2, 1:1, 2:3, 3:4, 9:16, 9:21 The aspect ratio of the generated media.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Flux Kontext Max Multi

- **Model ID:** `wavespeed-ai/flux-kontext-max/multi`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/flux-kontext-max/multi`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/flux-kontext-max-multi

**Request Parameters**

- `images`: array Yes [] - URL of images to use while generating the image.
- `prompt`: string Yes - The positive prompt for the generation.
- `guidance_scale`: number No 3.5 1.0 ~ 20.0 The guidance scale to use for the generation.
- `aspect_ratio`: string No - 21:9, 16:9, 4:3, 3:2, 1:1, 2:3, 3:4, 9:16, 9:21 The aspect ratio of the generated media.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Flux Kontext Max Text To Image

- **Model ID:** `wavespeed-ai/flux-kontext-max/text-to-image`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/flux-kontext-max/text-to-image`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/flux-kontext-max-text-to-image

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation.
- `guidance_scale`: number No 3.5 1.0 ~ 20.0 The guidance scale to use for the generation.
- `aspect_ratio`: string No 1:1 21:9, 16:9, 4:3, 3:2, 1:1, 2:3, 3:4, 9:16, 9:21 The aspect ratio of the generated media.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Flux Kontext Pro

- **Model ID:** `wavespeed-ai/flux-kontext-pro`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/flux-kontext-pro`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/flux-kontext-pro

**Request Parameters**

- `image`: string Yes - The image to generate an image from.
- `prompt`: string Yes - The positive prompt for the generation.
- `guidance_scale`: number No 3.5 1.0 ~ 20.0 The guidance scale to use for the generation. aspect_ratio No - 21:9, 16:9, 4:3, 3:2, 1:1, 2:3, 3:4, 9:16, 9:21 The aspect ratio of the generated media.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Flux Kontext Pro Multi

- **Model ID:** `wavespeed-ai/flux-kontext-pro/multi`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/flux-kontext-pro/multi`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/flux-kontext-pro-multi

**Request Parameters**

- `images`: array Yes [] - URL of images to use while generating the image.
- `prompt`: string Yes - The positive prompt for the generation.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation.
- `guidance_scale`: number No 3.5 1.0 ~ 20.0 The guidance scale to use for the generation. aspect_ratio No - 21:9, 16:9, 4:3, 3:2, 1:1, 2:3, 3:4, 9:16, 9:21 The aspect ratio of the generated media.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Flux Kontext Pro Text To Image

- **Model ID:** `wavespeed-ai/flux-kontext-pro/text-to-image`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/flux-kontext-pro/text-to-image`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/flux-kontext-pro-text-to-image

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `aspect_ratio`: string No 1:1 21:9, 16:9, 4:3, 3:2, 1:1, 2:3, 3:4, 9:16, 9:21 The aspect ratio of the generated media.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation.
- `guidance_scale`: number No 3.5 1.0 ~ 20.0 The guidance scale to use for the generation.
- `safety_tolerance`: string No 2 1, 2, 3, 4, 5 The safety tolerance level for the generated image. 1 being the most strict and 5 being the most permissive.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Flux Pulid

- **Model ID:** `wavespeed-ai/flux-krea-dev-lora`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/flux-krea-dev-lora`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/flux-pulid

**Request Parameters**

- `image`: string Yes -
- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 1024*1024 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `guidance_scale`: number No 3.5 0.0 ~ 20.0 The guidance scale to use for the generation.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `output_format`: string No jpeg jpeg, png, webp The format of the output image.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Flux Krea Dev LoRA

- **Model ID:** `wavespeed-ai/flux-krea-dev-lora`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/flux-krea-dev-lora`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/flux-krea-dev-lora

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `image`: string No -
- `size`: string No 1024*1024 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `loras`: array No max 3 items List of LoRAs to apply (max 3). loras[].path string Yes - Path to the LoRA model loras[].scale float Yes - 0.0 ~ 4.0 Scale of the LoRA model
- `strength`: number No 0.8 0.00 ~ 1.00 Strength indicates extent to transform the reference image.
- `guidance_scale`: number No 3.5 0.0 ~ 20.0 The guidance scale to use for the generation.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `output_format`: string No jpeg jpeg, png, webp The format of the output image.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Flux Redux Dev

- **Model ID:** `wavespeed-ai/flux-redux-dev`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/flux-redux-dev`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/flux-redux-dev

**Request Parameters**

- `image`: string Yes - The URL of the image to generate an image from.
- `size`: string No 1024*1024 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `num_inference_steps`: integer No 28 1 ~ 50 The number of inference steps to perform.
- `guidance_scale`: number No 3.5 1 ~ 20 The guidance scale to use for the generation.
- `num_images`: integer No 1 1 ~ 4 The number of images to generate.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation.
- `output_format`: string No jpeg jpeg, png, webp The format of the output image.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Flux Redux Pro

- **Model ID:** `wavespeed-ai/flux-redux-pro`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/flux-redux-pro`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/flux-redux-pro

**Request Parameters**

- `image`: string Yes - The URL of the image to generate an image from.
- `prompt`: string No - The positive prompt for the generation.
- `size`: string No 1024*1024 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation.
- `num_inference_steps`: integer No 28 1 ~ 50 The number of inference steps to perform.
- `guidance_scale`: number No 3.5 1.0 ~ 5.0 The guidance scale to use for the generation.

### Flux Schnell

- **Model ID:** `wavespeed-ai/flux-schnell`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/flux-schnell`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/flux-schnell

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `image`: string No -
- `mask_image`: string No - The mask image tells the model where to generate new pixels (white) and where to preserve the original image (black). It acts as a stencil or guide for targeted image editing.
- `strength`: number No 0.8 0.00 ~ 1.00 Strength indicates extent to transform the reference image.
- `size`: string No 1024*1024 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `num_images`: integer No 1 1 ~ 4 The number of images to generate.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `output_format`: string No jpeg jpeg, png, webp The format of the output image.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Flux Schnell LoRA

- **Model ID:** `wavespeed-ai/flux-schnell-lora`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/flux-schnell-lora`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/flux-schnell-lora

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `image`: string No -
- `mask_image`: string No - The mask image tells the model where to generate new pixels (white) and where to preserve the original image (black). It acts as a stencil or guide for targeted image editing.
- `strength`: number No 0.8 0.00 ~ 1.00 Strength indicates extent to transform the reference image.
- `loras`: array No max 3 items List of LoRAs to apply (max 3). loras[].path string Yes - Path to the LoRA model loras[].scale float Yes - 0.0 ~ 4.0 Scale of the LoRA model
- `size`: string No 1024*1024 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `num_images`: integer No 1 1 ~ 4 The number of images to generate.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `output_format`: string No jpeg jpeg, png, webp The format of the output image.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Flux Srpo

- **Model ID:** `wavespeed-ai/flux-srpo`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/flux-srpo`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/flux-srpo

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 1024*1024 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `strength`: number No 0.8 0.00 ~ 1.00 Strength indicates extent to transform the reference image.
- `num_inference_steps`: integer No 28 1 ~ 50 The number of inference steps to perform.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `guidance_scale`: number No 3.5 1.0 ~ 20.0 The guidance scale to use for the generation.
- `output_format`: string No jpeg jpeg, png The format of the output image.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Flux Srpo Image To Image

- **Model ID:** `wavespeed-ai/flux-srpo/image-to-image`
- **Operation:** `edit_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/flux-srpo/image-to-image`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/flux-srpo-image-to-image

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `image`: string No - The image to generate an image from.
- `strength`: number No 0.8 0.00 ~ 1.00 Strength indicates extent to transform the reference image.
- `num_inference_steps`: integer No 28 1 ~ 50 The number of inference steps to perform.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `guidance_scale`: number No 3.5 1.0 ~ 20.0 The guidance scale to use for the generation.
- `output_format`: string No jpeg jpeg, png The format of the output image.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Framepack

- **Model ID:** `wavespeed-ai/framepack`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/framepack`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/framepack

**Request Parameters**

- `image`: string Yes - The URL of the video to generate the audio for.
- `prompt`: string Yes - The positive prompt for the generation.
- `negative_prompt`: string No - The negative prompt for the generation.
- `aspect_ratio`: string No 16:9 16:9, 9:16 The aspect ratio of the generated media.
- `resolution`: string No 720p 720p, 480p The resolution of the video to generate. 720p generations cost 1.5x more than 480p generations.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation.
- `num_inference_steps`: integer No 25 4 ~ 50 The number of inference steps to perform.
- `num_frames`: integer No 180 30 ~ 1800 The duration of the audio to generate.
- `guidance_scale`: number No 10 0 ~ 32 The guidance scale to use for the generation.

### Ghibli

- **Model ID:** `wavespeed-ai/ghibli`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/ghibli`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/ghibli

**Request Parameters**

- `image`: string Yes - The image to generate an image from.
- `output_format`: string No jpeg jpeg, png, webp The format of the output image.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Ghibli Filter Image

- **Model ID:** `wavespeed-ai/ghibli-filter/image`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/ghibli-filter/image`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/ghibli-filter-image

**Request Parameters**

- `image`: string Yes - The URL of the input image.

### Hidream E1 Full

- **Model ID:** `wavespeed-ai/hidream-e1-full`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/hidream-e1-full`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/hidream-e1-full

**Request Parameters**

- `image`: string Yes - The image to edit.
- `prompt`: string Yes - The positive prompt for the generation.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `output_format`: string No jpeg jpeg, png, webp The format of the output image.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Hidream I1 Dev

- **Model ID:** `wavespeed-ai/hidream-i1-dev`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/hidream-i1-dev`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/hidream-i1-dev

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 1024*1024 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `output_format`: string No jpeg jpeg, png, webp The format of the output image.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Hidream I1 Full

- **Model ID:** `wavespeed-ai/hidream-i1-full`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/hidream-i1-full`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/hidream-i1-full

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 1024*1024 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `output_format`: string No jpeg jpeg, png, webp The format of the output image.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Hidream O1 Image Dev Edit

- **Model ID:** `wavespeed-ai/hidream-o1-image-dev/edit`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/hidream-o1-image-dev/edit`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/hidream-o1-image-dev-edit

**Request Parameters**

- `prompt`: string Yes - Text prompt for image generation.
- `images`: array Yes [] - Reference images for image editing or subject-driven personalization.
- `size`: string No 2048*2048 256 ~ 4096 per dimension Specify the width and height pixel values of the generated image.Total pixel value range: [2560*1440, 4096*4096]
- `output_format`: string No jpeg png, jpeg, webp Output image format.
- `seed`: integer No - -1 ~ 2147483647
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Hidream O1 Image Dev Text To Image

- **Model ID:** `wavespeed-ai/hidream-o1-image-dev/text-to-image`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/hidream-o1-image-dev/text-to-image`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/hidream-o1-image-dev-text-to-image

**Request Parameters**

- `prompt`: string Yes - Text prompt for image generation.
- `size`: string No 2048*2048 256 ~ 4096 per dimension Specify the width and height pixel values of the generated image.Total pixel value range: [2560*1440, 4096*4096]
- `output_format`: string No jpeg png, jpeg, webp Output image format.
- `seed`: integer No - -1 ~ 2147483647
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Hidream O1 Image Edit

- **Model ID:** `wavespeed-ai/hidream-o1-image/edit`
- **Operation:** `edit_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/hidream-o1-image/edit`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/hidream-o1-image-edit

**Request Parameters**

- `prompt`: string Yes - Text prompt for image generation.
- `images`: array Yes [] - Reference images for image editing or subject-driven personalization.
- `size`: string No 2048*2048 256 ~ 4096 per dimension Specify the width and height pixel values of the generated image.Total pixel value range: [2560*1440, 4096*4096]
- `output_format`: string No jpeg png, jpeg, webp Output image format.
- `seed`: integer No - -1 ~ 2147483647
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Hidream O1 Image Text To Image

- **Model ID:** `wavespeed-ai/hidream-o1-image/text-to-image`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/hidream-o1-image/text-to-image`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/hidream-o1-image-text-to-image

**Request Parameters**

- `prompt`: string Yes - Text prompt for image generation.
- `size`: string No 2048*2048 256 ~ 4096 per dimension Specify the width and height pixel values of the generated image.Total pixel value range: [2560*1440, 4096*4096]
- `output_format`: string No jpeg png, jpeg, webp Output image format.
- `seed`: integer No - -1 ~ 2147483647
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Hunyuan Image 2.1

- **Model ID:** `wavespeed-ai/hunyuan-image-2.1`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/hunyuan-image-2.1`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/hunyuan-image-2.1

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 1024*1024 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `output_format`: string No jpeg jpeg, png, webp The format of the output image.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Hunyuan Image 3

- **Model ID:** `wavespeed-ai/hunyuan-image-3`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/hunyuan-image-3`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/hunyuan-image-3

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 1024*1024 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Hunyuan Image 3 Instruct Edit

- **Model ID:** `wavespeed-ai/hunyuan-image-3-instruct/edit`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/hunyuan-image-3-instruct/edit`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/hunyuan-image-3-instruct-edit

**Request Parameters**

- `images`: array Yes [] 1 ~ 2 items URLs of the input images to edit (up to 2 images).
- `prompt`: string Yes - The text prompt describing the desired edit to the image.
- `size`: string No - 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Hunyuan Image 3 Instruct Text To Image

- **Model ID:** `wavespeed-ai/hunyuan-image-3-instruct/text-to-image`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/hunyuan-image-3-instruct/text-to-image`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/hunyuan-image-3-instruct-text-to-image

**Request Parameters**

- `prompt`: string Yes - The text prompt describing the desired image.
- `size`: string No 1024*1024 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Ic Light

- **Model ID:** `wavespeed-ai/ic-light`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/ic-light`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/ic-light

**Request Parameters**

- `image`: string Yes - Upload the image you want to relight
- `prompt`: string Yes - Describe the lighting effect you want to apply to the image
- `lighting_direction`: string No None None, Left, Right, Top, Bottom Choose the direction of the light source

### Image Body Swap

- **Model ID:** `wavespeed-ai/image-body-swap`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/image-body-swap`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/image-body-swap

**Request Parameters**

- `image`: string Yes - The URL of the face/head image.
- `body_image`: string Yes - - The URL of the target body image.

### Image Captioner

- **Model ID:** `wavespeed-ai/image-captioner`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/image-captioner`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/image-captioner

**Request Parameters**

- `image`: string No - Image to caption
- `detail_level`: string No medium low, medium, high Level of detail for the caption.
- `focus`: string No - - Specific area or subject to focus on in the caption (optional).
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Image Converter

- **Model ID:** `wavespeed-ai/image-converter`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/image-converter`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/image-converter

**Request Parameters**

- `image`: string Yes - The URL of the input image.
- `output_format`: string Yes - jpeg, jpg, png, webp, bmp, tiff, gif, avif The target format to convert the image to (e.g. jpeg, jpg, png, webp, bmp, tiff, gif, avif).

### Image Eraser

- **Model ID:** `wavespeed-ai/image-eraser`
- **Operation:** `edit_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/image-eraser`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/image-eraser

**Request Parameters**

- `image`: string Yes - The image to generate an image from.
- `mask_image`: string No - The mask image to indicate the area to be erased. The area to be erased should be in white color and the area to be kept should be in black color.
- `prompt`: string No - The text prompt for specifying the objects or areas to be removed from the image. For example, 'dog' or 'hat'.
- `output_format`: string No jpeg jpeg, png, webp The format of the output image.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Image Face Blur

- **Model ID:** `wavespeed-ai/image-face-blur`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/image-face-blur`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/image-face-blur

**Request Parameters**

- `image`: string Yes - The URL of the input image.

### Image Face Swap

- **Model ID:** `wavespeed-ai/image-face-swap`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/image-face-swap`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/image-face-swap

**Request Parameters**

- `image`: string Yes - The image that contains the face to be replaced.
- `face_image`: string Yes - - The face image as reference.
- `target_index`: integer No - 0 ~ 10 0 = largest face. To switch to another target face - switch to index 1, e.t.c.
- `output_format`: string No jpeg jpeg, png, webp The format of the output image.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Image Face Swap Pro

- **Model ID:** `wavespeed-ai/image-face-swap-pro`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/image-face-swap-pro`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/image-face-swap-pro

**Request Parameters**

- `image`: string Yes - The image that contains the face to be replaced.
- `face_image`: string Yes - - The face image as reference.
- `output_format`: string No jpeg jpeg, png, webp The format of the output image.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Image Head Swap

- **Model ID:** `wavespeed-ai/image-head-swap`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/image-head-swap`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/image-head-swap

**Request Parameters**

- `image`: string Yes - The image that contains the face to be replaced.
- `face_image`: string Yes - - The face image as reference.
- `output_format`: string No jpeg jpeg, png, webp The format of the output image.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Image Text Remover

- **Model ID:** `wavespeed-ai/image-text-remover`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/image-text-remover`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/image-text-remover

**Request Parameters**

- `image`: string Yes - The URL of the image to generate an image from.
- `output_format`: string No jpeg jpeg, png, webp The format of the output image.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Image Translator

- **Model ID:** `wavespeed-ai/image-translator`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/image-translator`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/image-translator

**Request Parameters**

- `image`: string Yes - The URL of the image to generate an image from.
- `target_language`: string Yes english english, chinese-simplified, chinese-traditional, spanish, french, arabic, hindi, bengali, portuguese, russian, japanese, korean, german, italian, dutch, polish, turkish, vietnamese, thai, indonesian, malay, filipino, urd
- `output_format`: string No jpeg jpeg, png, webp The format of the output image.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Image Watermark Remover

- **Model ID:** `wavespeed-ai/image-watermark-remover`
- **Operation:** `edit_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/image-watermark-remover`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/image-watermark-remover

**Request Parameters**

- `image`: string Yes - The image to generate an image from.
- `output_format`: string No jpeg jpeg, png, webp The format of the output image.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Image Zoom Out

- **Model ID:** `wavespeed-ai/image-zoom-out`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/image-zoom-out`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/image-zoom-out

**Request Parameters**

- `image`: string Yes - The image to generate an image from.
- `size`: string No - 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `output_format`: string No jpeg jpeg, png, webp The format of the output image.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Imagen4

- **Model ID:** `wavespeed-ai/imagen4`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/imagen4`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/imagen4

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `negative_prompt`: string No - The negative prompt for the generation.
- `aspect_ratio`: string No 1:1 1:1, 16:9, 9:16, 3:4, 4:3 The aspect ratio of the generated media.
- `num_images`: integer No 1 1 ~ 4 The number of images to generate.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation.

### Infinite You

- **Model ID:** `wavespeed-ai/infinite-you`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/infinite-you`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/infinite-you

**Request Parameters**

- `target_image`: string Yes - - URL of the target image where the face will be swapped
- `source_image`: string Yes - - URL of the source face image to extract identity from

### Infinitetalk

- **Model ID:** `wavespeed-ai/infinitetalk`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/infinitetalk`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/infinitetalk

**Request Parameters**

- `image`: string Yes - The image for generating the output.
- `audio`: string Yes - - The audio for generating the output.
- `mask_image`: string No - Optional mask image to specify the person in the image to animate.
- `prompt`: string No - The positive prompt for the generation.
- `resolution`: string No 480p 480p, 720p The resolution of the output video.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Infinitetalk Fast

- **Model ID:** `wavespeed-ai/infinitetalk-fast`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/infinitetalk-fast`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/infinitetalk-fast

**Request Parameters**

- `image`: string Yes - The image for generating the output.
- `audio`: string Yes - - The audio for generating the output.
- `mask_image`: string No - Optional mask image to specify the person in the image to animate.
- `prompt`: string No - The positive prompt for the generation.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Infinitetalk Fast Multi

- **Model ID:** `wavespeed-ai/infinitetalk-fast/multi`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/infinitetalk-fast/multi`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/infinitetalk-fast-multi

**Request Parameters**

- `image`: string Yes - The image for generating the output.
- `left_audio`: string Yes - - The audio of the persion on the left for generating the output.
- `right_audio`: string Yes - - The audio of the persion on the right for generating the output.
- `prompt`: string No - The positive prompt for the generation.
- `order`: string No meanwhile meanwhile, left_right, right_left The order of the two audio sources in the output video, "meanwhile" means both audio sources will play at the same time, "left_right" means the left audio will play first then the right 
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Infinitetalk Multi

- **Model ID:** `wavespeed-ai/infinitetalk/multi`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/infinitetalk/multi`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/infinitetalk-multi

**Request Parameters**

- `image`: string Yes - The image for generating the output.
- `left_audio`: string Yes - - The audio of the persion on the left for generating the output.
- `right_audio`: string Yes - - The audio of the persion on the right for generating the output.
- `prompt`: string No - The positive prompt for the generation.
- `order`: string No meanwhile meanwhile, left_right, right_left The order of the two audio sources in the output video, "meanwhile" means both audio sources will play at the same time, "left_right" means the left audio will play first then the right 
- `resolution`: string No 480p 480p, 720p The resolution of the output video.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Instant Character

- **Model ID:** `wavespeed-ai/instant-character`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/instant-character`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/instant-character

**Request Parameters**

- `image`: string Yes - The image URL to generate an image from. Needs to match the dimensions of the mask.
- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 1024*1024 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation.

### Jib Mix Qwen Image Text To Image

- **Model ID:** `wavespeed-ai/jib-mix-qwen-image/text-to-image`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/jib-mix-qwen-image/text-to-image`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/jib-mix-qwen-image-text-to-image

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 1024*1024 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `output_format`: string No jpeg jpeg, png, webp The format of the output image.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Jib Mix Qwen Image Text To Image LoRA

- **Model ID:** `wavespeed-ai/jib-mix-qwen-image/text-to-image-lora`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/jib-mix-qwen-image/text-to-image-lora`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/jib-mix-qwen-image-text-to-image-lora

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 1024*1024 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `loras`: array No max 3 items List of LoRAs to apply (maximum 3). loras[].path string Yes - Path to the LoRA model loras[].scale float Yes - 0.0 ~ 4.0 Scale of the LoRA model
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `output_format`: string No jpeg jpeg, png, webp The format of the output image.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Joyai Image Edit

- **Model ID:** `wavespeed-ai/joyai-image/edit`
- **Operation:** `edit_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/joyai-image/edit`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/joyai-image-edit

**Request Parameters**

- `prompt`: string Yes - The edit instruction describing what changes to make to the image.
- `image`: string Yes - URL of the input image to edit.

### Latentsync

- **Model ID:** `wavespeed-ai/latentsync`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/latentsync`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/latentsync

**Request Parameters**

- `audio`: string Yes - - The URL of the audio to be synchronized.
- `video`: string Yes - The URL of the video to be synchronized.

### Longcat Image Edit

- **Model ID:** `wavespeed-ai/longcat-image/edit`
- **Operation:** `edit_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/longcat-image/edit`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/longcat-image-edit

**Request Parameters**

- `image`: string Yes - The image to generate an image from.
- `prompt`: string Yes - The prompt to edit the image with.
- `output_format`: string No jpeg jpeg, png, webp The format of the output image.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Longcat Image Text To Image

- **Model ID:** `wavespeed-ai/longcat-image/text-to-image`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/longcat-image/text-to-image`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/longcat-image-text-to-image

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 1024*1024 256 ~ 1536 per dimension The size of the generated image in pixels (width*height).
- `output_format`: string No jpeg jpeg, png, webp The format of the output image.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Ltx 2 19b Control

- **Model ID:** `wavespeed-ai/ltx-2-19b/control`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/ltx-2-19b/control`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/ltx-2-19b-control

**Request Parameters**

- `video`: string Yes - The video for generating the output.
- `image`: string No - Optional reference image for appearance guidance. If not provided, the model generates based on the prompt.
- `prompt`: string No - The positive prompt for the generation.
- `mode`: string No pose pose, depth, canny The control mode for video generation. Pose: skeleton/pose guidance. Canny: edge detection guidance. Depth: depth map guidance.
- `audio_mode`: string No preserve preserve, generate, none Audio handling mode. Preserve: keep original audio from input video. Generate: create new synchronized audio. None: output video without audio.
- `resolution`: string No 720p 480p, 720p, 1080p The resolution of the output video.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Lynx

- **Model ID:** `wavespeed-ai/lynx`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/lynx`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/lynx

**Request Parameters**

- `image`: string Yes - The image for generating the output.
- `prompt`: string Yes - The positive prompt for the generation.
- `aspect_ratio`: string No 16:9 16:9, 9:16, 1:1 The aspect ratio of the generated media.

### Magi 1 24b

- **Model ID:** `wavespeed-ai/magi-1-24b`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/magi-1-24b`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/magi-1-24b

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `image`: string No - URL of an input image to represent the first frame of the video. If the input image does not match the chosen aspect ratio, it is resized and center cropped.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation.

### Minicpm V Image

- **Model ID:** `wavespeed-ai/minicpm-v/image`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/minicpm-v/image`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/minicpm-v-image

**Request Parameters**

- `image`: string Yes - Image to be analyzed.
- `preset_prompt`: string No describe describe, caption Preset prompt for image analysis.
- `custom_prompt`: string No - - Custom prompt for image analysis.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Molmo2 Image Captioner

- **Model ID:** `wavespeed-ai/molmo2/image-captioner`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/molmo2/image-captioner`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/molmo2-image-captioner

**Request Parameters**

- `image`: string Yes - Input image URL for captioning. Supports common image formats (JPEG, PNG, WebP).
- `detail_level`: string No medium low, medium, high Level of detail in the generated caption. Low: brief summary. Medium: balanced description. High: comprehensive, detailed analysis.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Molmo2 Image Content Moderator

- **Model ID:** `wavespeed-ai/molmo2/image-content-moderator`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/molmo2/image-content-moderator`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/molmo2-image-content-moderator

**Request Parameters**

- `image`: string Yes - Image URL to moderate and analyze for safety compliance. Supports JPEG, PNG, WebP formats.
- `text`: string No - - Optional text prompt or question about the image content for contextual analysis.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Molmo2 Image Qa

- **Model ID:** `wavespeed-ai/molmo2/image-qa`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/molmo2/image-qa`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/molmo2-image-qa

**Request Parameters**

- `images`: array Yes [] 1 ~ 2 items Array of image URLs for question answering (1-2 images). Supports common image formats (JPEG, PNG, WebP).
- `text`: string Yes - - Your question about the image(s).
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Molmo2 Prompt Optimizer

- **Model ID:** `wavespeed-ai/molmo2/prompt-optimizer`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/molmo2/prompt-optimizer`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/molmo2-prompt-optimizer

**Request Parameters**

- `image`: string No - Image to use as context for prompt optimization.
- `text`: string No - - Text to expand or use as context for prompt optimization.
- `style`: string No default default, artistic, photographic, technical, anime, realistic Style or tone to apply to the optimized prompt.
- `mode`: string No image image, video The aim of the optimization, either for image or video generation.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Molmo2 Text Content Moderator

- **Model ID:** `wavespeed-ai/molmo2/text-content-moderator`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/molmo2/text-content-moderator`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/molmo2-text-content-moderator

**Request Parameters**

- `text`: string Yes - - Text content to moderate and analyze for safety compliance.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Moondream3 Preview Caption

- **Model ID:** `wavespeed-ai/moondream3-preview/caption`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/moondream3-preview/caption`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/moondream3-preview-caption

**Request Parameters**

- `image`: string Yes - Image to be described. Provide an HTTPS URL or upload an image file.
- `length`: string No normal normal, short, long Caption length. Options: 'short', 'normal', or 'long'.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result before returning the response. This property is only available through the API.

### Moondream3 Preview Detect

- **Model ID:** `wavespeed-ai/moondream3-preview/detect`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/moondream3-preview/detect`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/moondream3-preview-detect

**Request Parameters**

- `image`: string Yes - Image to analyze. Provide an HTTPS URL or upload an image file.
- `prompt`: string Yes - Object to detect in the image (e.g., 'car', 'person', 'dog').
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result before returning the response. This property is only available through the API.

### Moondream3 Preview Point

- **Model ID:** `wavespeed-ai/moondream3-preview/point`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/moondream3-preview/point`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/moondream3-preview-point

**Request Parameters**

- `image`: string Yes - Image to analyze. Provide an HTTPS URL or upload an image file.
- `prompt`: string Yes - Object to locate in the image (e.g., 'car', 'person', 'dog').
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result before returning the response. This property is only available through the API.

### Moondream3 Preview Query

- **Model ID:** `wavespeed-ai/moondream3-preview/query`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/moondream3-preview/query`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/moondream3-preview-query

**Request Parameters**

- `image`: string Yes - Image to be analyzed. Provide an HTTPS URL or upload an image file.
- `prompt`: string Yes - Your question about the image.
- `reasoning`: boolean No false - Enable chain-of-thought reasoning to get more detailed explanations.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result before returning the response. This property is only available through the API.

### Multitalk

- **Model ID:** `wavespeed-ai/multitalk`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/multitalk`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/multitalk

**Request Parameters**

- `image`: string Yes - The image for generating the output.
- `audio`: string Yes - - The audio for generating the output.
- `prompt`: string No - The positive prompt for the generation.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Neta Lumina

- **Model ID:** `wavespeed-ai/neta-lumina`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/neta-lumina`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/neta-lumina

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 1024*1024 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `output_format`: string No jpeg jpeg, png, webp The format of the output image.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Nucleus Image Text To Image

- **Model ID:** `wavespeed-ai/nucleus-image/text-to-image`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/nucleus-image/text-to-image`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/nucleus-image-text-to-image

**Request Parameters**

- `prompt`: string Yes - The prompt to use for generating the image.
- `negative_prompt`: string No - The negative prompt to use for generation.
- `aspect_ratio`: string No 1:1 1:1, 16:9, 9:16, 4:3, 3:4, 3:2, 2:3 The output aspect ratio. Nucleus-Image supports a fixed set of aspect-ratio presets.
- `num_images`: integer No 1 1 ~ 2 The number of images to generate.
- `num_inference_steps`: integer No 50 1 ~ 100 The number of inference steps to perform.
- `guidance_scale`: number No 8 0.0 ~ 20.0 The classifier-free guidance scale.
- `output_format`: string No png jpeg, png The format of the generated image.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.
- `seed`: integer No - -1 ~ 2147483647 Seed for reproducible generation.

### Openai Whisper Turbo

- **Model ID:** `wavespeed-ai/openai-whisper`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/openai-whisper`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/openai-whisper-turbo

**Request Parameters**

- `audio`: string Yes - - Audio file to transcribe. Provide an HTTPS URL or upload a file (MP3, WAV, FLAC up to 60 minutes).
- `language`: string No auto auto, af, am, ar, as, az, ba, be, bg, bn, bo, br, bs, ca, cs, cy, da, de, el, en, es, et, eu, fa, fi, fo, fr, gl, gu, ha, haw, he, hi, hr, ht, hu, hy, id, is, it, ja, jw, ka, kk, km, kn, ko, la, lb, ln, lo, lt, lv, mg, mi, mk
- `prompt`: string No - An optional text to provide as a prompt to guide the model's style or continue a previous audio segment. The prompt should be in the same language as the audio.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Openai Whisper

- **Model ID:** `wavespeed-ai/openai-whisper`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/openai-whisper`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/openai-whisper

**Request Parameters**

- `audio`: string Yes - - Audio file to transcribe. Provide an HTTPS URL or upload a file (MP3, WAV, FLAC up to 60 minutes).
- `language`: string No auto auto, af, am, ar, as, az, ba, be, bg, bn, bo, br, bs, ca, cs, cy, da, de, el, en, es, et, eu, fa, fi, fo, fr, gl, gu, ha, haw, he, hi, hr, ht, hu, hy, id, is, it, ja, jw, ka, kk, km, kn, ko, la, lb, ln, lo, lt, lv, mg, mi, mk
- `task`: string No transcribe transcribe, translate The task to perform. 'transcribe' to the source language or 'translate' to English.
- `enable_timestamps`: boolean No false - Enable to generate word-level timestamps for the transcription. Note: This may increase processing time.
- `prompt`: string No - An optional text to provide as a prompt to guide the model's style or continue a previous audio segment. The prompt should be in the same language as the audio.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Paddle Ocr

- **Model ID:** `wavespeed-ai/paddle-ocr`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/paddle-ocr`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/paddle-ocr

**Request Parameters**

- `image`: string Yes - Document image to parse. Supports text, tables, formulas, and charts recognition in 109 languages.
- `output_format`: string No markdown json, markdown Output format: 'json' for structured data or 'markdown' for human-readable text.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Patina Image To Map

- **Model ID:** `wavespeed-ai/patina/image-to-map`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/patina/image-to-map`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/patina-image-to-map

**Request Parameters**

- `image`: string Yes - URL of the input image (photograph or render) to generate PBR material maps from.

### Patina Material

- **Model ID:** `wavespeed-ai/patina/material`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/patina/material`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/patina-material

**Request Parameters**

- `prompt`: string Yes - Text description of the material to generate (e.g., 'weathered oak wood planks', 'cracked desert clay').
- `size`: string No 1024*1024 256 ~ 1536 per dimension The size of the generated material maps in pixels (width*height).
- `tiling_mode`: string No both both, horizontal, vertical Seamless tiling direction. 'both' tiles in all directions; 'horizontal' or 'vertical' tiles only along one axis.

### Patina Material Extract

- **Model ID:** `wavespeed-ai/patina/material-extract`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/patina/material-extract`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/patina-material-extract

**Request Parameters**

- `image`: string Yes - URL of the reference image to extract a tiling material from.
- `prompt`: string Yes - Text description guiding which texture to extract (e.g., 'the stone wall surface', 'the wood grain pattern').
- `size`: string No 1024*1024 256 ~ 1536 per dimension The size of the generated material maps in pixels (width*height).
- `tiling_mode`: string No both both, horizontal, vertical Seamless tiling direction. 'both' tiles in all directions; 'horizontal' or 'vertical' tiles only along one axis.

### Phota Edit

- **Model ID:** `wavespeed-ai/phota/edit`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/phota/edit`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/phota-edit

**Request Parameters**

- `prompt`: string Yes - Text description of the desired image.
- `images`: array No [] 1 ~ 10 items List of URLs of input images for editing. The maximum number of images is 10.
- `resolution`: string No 1K 1K, 4K Resolution of the generated image.
- `num_images`: integer No 1 1 ~ 4 Number of images to generate.
- `aspect_ratio`: string No auto auto, 1:1, 16:9, 4:3, 3:4, 9:16 Aspect ratio of the generated image.
- `output_format`: string No jpeg jpeg, png, webp The format of the generated image.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Phota Enhance

- **Model ID:** `wavespeed-ai/phota/enhance`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/phota/enhance`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/phota-enhance

**Request Parameters**

- `image`: string Yes - Input image supports both URL and Base64 format.
- `num_images`: integer No 1 1 ~ 4 Number of images to generate.
- `output_format`: string No jpeg jpeg, png, webp The format of the generated image.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Phota Text To Image

- **Model ID:** `wavespeed-ai/phota/text-to-image`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/phota/text-to-image`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/phota-text-to-image

**Request Parameters**

- `prompt`: string Yes - Text description of the desired image.
- `resolution`: string No 1K 1K, 4K Resolution of the generated image.
- `num_images`: integer No 1 1 ~ 4 Number of images to generate.
- `aspect_ratio`: string No auto auto, 1:1, 16:9, 4:3, 3:4, 9:16 Aspect ratio of the generated image.
- `output_format`: string No jpeg jpeg, png, webp The format of the generated image.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Prefect Pony Xl

- **Model ID:** `wavespeed-ai/prefect-pony-xl`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/prefect-pony-xl`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/prefect-pony-xl

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 1024*1024 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `output_format`: string No jpeg jpeg, png, webp The format of the output image.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Prompt Optimizer

- **Model ID:** `wavespeed-ai/prompt-optimizer`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/prompt-optimizer`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/prompt-optimizer

**Request Parameters**

- `image`: string No - Image to use as context for prompt optimization.
- `text`: string No - - Text to expand or use as context for prompt optimization.
- `style`: string No default default, artistic, photographic, technical, anime, realistic Style or tone to apply to the optimized prompt.
- `mode`: string No image image, video The aim of the optimization, either for image or video generation.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Qwen Image 2.0 Pro Edit

- **Model ID:** `wavespeed-ai/qwen-image-2.0-pro/edit`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/qwen-image-2.0-pro/edit`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/qwen-image-2.0-pro-edit

**Request Parameters**

- `images`: array Yes [] 1 ~ 6 items Reference images for editing (1-6 images, 384-3072px each dimension)
- `prompt`: string Yes - Text prompt describing the desired edit, supports Chinese and English (max 800 characters)
- `size`: string No - 256 ~ 2048 per dimension Image dimensions in width*height format (e.g., 1024*1024, 1280*720)
- `seed`: integer No -1 -1 ~ 2147483647 Random seed for reproducibility (-1 for random, 0-2147483647 for specific seed)

### Qwen Image 2.0 Pro Text To Image

- **Model ID:** `wavespeed-ai/qwen-image-2.0-pro/text-to-image`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/qwen-image-2.0-pro/text-to-image`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/qwen-image-2.0-pro-text-to-image

**Request Parameters**

- `prompt`: string Yes - Text prompt describing the desired image, supports Chinese and English (max 800 characters)
- `size`: string No 1024*1024 256 ~ 2048 per dimension Image dimensions in width*height format (e.g., 1024*1024, 1280*720)
- `seed`: integer No -1 -1 ~ 2147483647 Random seed for reproducibility (-1 for random, 0-2147483647 for specific seed)

### Qwen Image 2.0 Edit

- **Model ID:** `wavespeed-ai/qwen-image-2.0/edit`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/qwen-image-2.0/edit`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/qwen-image-2.0-edit

**Request Parameters**

- `images`: array Yes [] 1 ~ 6 items Reference images for editing (1-6 images, 384-3072px each dimension)
- `prompt`: string Yes - Text prompt describing the desired edit, supports Chinese and English (max 800 characters)
- `size`: string No - 256 ~ 2048 per dimension Image dimensions in width*height format (e.g., 1024*1024, 1280*720)
- `seed`: integer No -1 -1 ~ 2147483647 Random seed for reproducibility (-1 for random, 0-2147483647 for specific seed)

### Qwen Image 2.0 Text To Image

- **Model ID:** `wavespeed-ai/qwen-image-2.0/text-to-image`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/qwen-image-2.0/text-to-image`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/qwen-image-2.0-text-to-image

**Request Parameters**

- `prompt`: string Yes - Text prompt describing the desired image, supports Chinese and English (max 800 characters)
- `size`: string No 1024*1024 256 ~ 2048 per dimension Image dimensions in width*height format (e.g., 1024*1024, 1280*720)
- `seed`: integer No -1 -1 ~ 2147483647 Random seed for reproducibility (-1 for random, 0-2147483647 for specific seed)

### Qwen Image Max Edit

- **Model ID:** `wavespeed-ai/qwen-image-max/edit`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/qwen-image-max/edit`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/qwen-image-max-edit

**Request Parameters**

- `images`: array Yes [] 1 ~ 6 items Reference images for editing (1-6 images, 384-3072px each dimension)
- `prompt`: string Yes - Text prompt describing the desired edit, supports Chinese and English (max 800 characters)
- `size`: string No - 256 ~ 1536 per dimension Image dimensions in width*height format (e.g., 1024*1024, 1280*720)
- `seed`: integer No -1 -1 ~ 2147483647 Random seed for reproducibility (-1 for random, 0-2147483647 for specific seed)

### Qwen Image Max Text To Image

- **Model ID:** `wavespeed-ai/qwen-image-max/text-to-image`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/qwen-image-max/text-to-image`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/qwen-image-max-text-to-image

**Request Parameters**

- `prompt`: string Yes - Text prompt describing the desired image, supports Chinese and English (max 800 characters)
- `size`: string No 1024*1024 256 ~ 1536 per dimension Image dimensions in width*height format (e.g., 1024*1024, 1280*720)
- `seed`: integer No -1 -1 ~ 2147483647 Random seed for reproducibility (-1 for random, 0-2147483647 for specific seed)

### Qwen Image Edit

- **Model ID:** `wavespeed-ai/qwen-image/edit`
- **Operation:** `edit_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/qwen-image/edit`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/qwen-image-edit

**Request Parameters**

- `image`: string Yes - The image to generate an image from.
- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No - 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `output_format`: string No jpeg jpeg, png, webp The format of the output image.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Qwen Image Edit 2509 Multiple Angles

- **Model ID:** `wavespeed-ai/qwen-image/edit-2509-multiple-angles`
- **Operation:** `edit_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/qwen-image/edit-2509-multiple-angles`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/qwen-image-edit-2509-multiple-angles

**Request Parameters**

- `images`: array Yes [] 1 ~ 3 items The input images. A maximum of 3 reference images can be uploaded.
- `horizontal_angle`: integer No - -90 ~ 90 Horizontal rotation angle in degrees. Controls the camera's horizontal position. Negative values rotate left, positive values rotate right. Values are rounded to the nearest valid angle: -90 (left), -45 (rotate left), 
- `vertical_angle`: integer No - -30 ~ 60 Vertical tilt angle (elevation) in degrees. Controls the camera's vertical position. Values are rounded to the nearest valid angle: -30 (low angle), 0 (eye level), 30 (elevated), 60 (high angle).
- `distance`: integer No 1 0 ~ 2 Camera distance/zoom level. 0 = close-up, 1 = medium shot, 2 = wide shot.
- `prompt`: string No - Optional additional prompt to guide the generation. This is appended to the angle prompt.
- `size`: string No - 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `output_format`: string No jpeg jpeg, png, webp The format of the output image.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Qwen Image Edit 2511

- **Model ID:** `wavespeed-ai/qwen-image/edit-2511`
- **Operation:** `edit_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/qwen-image/edit-2511`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/qwen-image-edit-2511

**Request Parameters**

- `images`: array Yes [] 1 ~ 3 items The images to edit. A maximum of 3 reference images can be uploaded.
- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No - 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `output_format`: string No jpeg jpeg, png, webp The format of the output image.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Qwen Image Edit 2511 LoRA

- **Model ID:** `wavespeed-ai/qwen-image/edit-2511-lora`
- **Operation:** `edit_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/qwen-image/edit-2511-lora`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/qwen-image-edit-2511-lora

**Request Parameters**

- `images`: array Yes [] 1 ~ 3 items The images to edit. A maximum of 3 reference images can be uploaded.
- `prompt`: string Yes - The positive prompt for the generation.
- `loras`: array No max undefined items Array of LoRA models to apply. Each LoRA can have a custom scale/weight. loras[].path string Yes - Path to the LoRA model loras[].scale float Yes - 0.0 ~ 4.0 Scale of the LoRA model
- `size`: string No - 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `output_format`: string No jpeg jpeg, png, webp The format of the output image.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Qwen Image Edit LoRA

- **Model ID:** `wavespeed-ai/qwen-image/edit-lora`
- **Operation:** `edit_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/qwen-image/edit-lora`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/qwen-image-edit-lora

**Request Parameters**

- `image`: string Yes - The image to generate an image from.
- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No - 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `loras`: array No max 3 items List of LoRAs to apply (maximum 3). loras[].path string Yes - Path to the LoRA model loras[].scale float Yes - 0.0 ~ 4.0 Scale of the LoRA model
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `output_format`: string No jpeg jpeg, png, webp The format of the output image.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Qwen Image Edit Multiple Angles

- **Model ID:** `wavespeed-ai/qwen-image/edit-multiple-angles`
- **Operation:** `edit_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/qwen-image/edit-multiple-angles`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/qwen-image-edit-multiple-angles

**Request Parameters**

- `images`: array Yes [] 1 ~ 3 items The input images. A maximum of 3 reference images can be uploaded.
- `horizontal_angle`: integer No - 0 ~ 359 Horizontal rotation angle (azimuth) in degrees. Controls the camera's horizontal position around the subject. Values are rounded to the nearest valid angle: 0 (front), 45 (front-right), 90 (right), 135 (back-right), 180
- `vertical_angle`: integer No - -30 ~ 60 Vertical tilt angle (elevation) in degrees. Controls the camera's vertical position. Values are rounded to the nearest valid angle: -30 (low angle), 0 (eye level), 30 (elevated), 60 (high angle).
- `distance`: integer No 1 0 ~ 2 Camera distance/zoom level. 0 = close-up, 1 = medium shot, 2 = wide shot.
- `prompt`: string No - Optional additional prompt to guide the generation. This is appended to the angle prompt.
- `size`: string No - 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `output_format`: string No jpeg jpeg, png, webp The format of the output image.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Qwen Image Edit Plus

- **Model ID:** `wavespeed-ai/qwen-image/edit-plus`
- **Operation:** `edit_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/qwen-image/edit-plus`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/qwen-image-edit-plus

**Request Parameters**

- `images`: array Yes [] 1 ~ 3 items The images to edit. A maximum of 3 reference images can be uploaded.
- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No - 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `output_format`: string No jpeg jpeg, png, webp The format of the output image.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Qwen Image Edit Plus LoRA

- **Model ID:** `wavespeed-ai/qwen-image/edit-plus-lora`
- **Operation:** `edit_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/qwen-image/edit-plus-lora`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/qwen-image-edit-plus-lora

**Request Parameters**

- `images`: array Yes [] 1 ~ 3 items The images to edit. A maximum of 3 reference images can be uploaded.
- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No - 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `loras`: array No max 3 items List of LoRAs to apply (maximum 3). loras[].path string Yes - Path to the LoRA model loras[].scale float Yes - 0.0 ~ 4.0 Scale of the LoRA model
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `output_format`: string No jpeg jpeg, png, webp The format of the output image.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Qwen Image Layered

- **Model ID:** `wavespeed-ai/qwen-image/layered`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/qwen-image/layered`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/qwen-image-layered

**Request Parameters**

- `image`: string Yes - The image to decompose into layers.
- `prompt`: string No - A text description of the image content.
- `num_layers`: integer No 4 2 ~ 8 The number of layers to generate (1-8).
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated before returning the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Qwen Image Text To Image

- **Model ID:** `wavespeed-ai/qwen-image/text-to-image`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/qwen-image/text-to-image`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/qwen-image-text-to-image

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 1024*1024 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `output_format`: string No jpeg jpeg, png, webp The format of the output image.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Qwen Image Text To Image 2512

- **Model ID:** `wavespeed-ai/qwen-image/text-to-image-2512`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/qwen-image/text-to-image-2512`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/qwen-image-text-to-image-2512

**Request Parameters**

- `prompt`: string Yes - Describe the image you want to create. Be specific about subject, style, composition, and mood for best results.
- `size`: string No 1024*1024 256 ~ 1536 per dimension The size of the generated image in pixels (width*height)
- `seed`: integer No -1 -1 ~ 2147483647 Random seed for reproducible results (same seed + prompt = same output)
- `output_format`: string No jpeg jpeg, png, webp The format of the generated image
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Qwen Image Text To Image 2512 LoRA

- **Model ID:** `wavespeed-ai/qwen-image/text-to-image-2512-lora`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/qwen-image/text-to-image-2512-lora`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/qwen-image-text-to-image-2512-lora

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 1024*1024 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `loras`: array No max 3 items List of LoRAs to apply (maximum 3). loras[].path string Yes - Path to the LoRA model loras[].scale float Yes - 0.0 ~ 4.0 Scale of the LoRA model
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `output_format`: string No jpeg jpeg, png, webp The format of the output image.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Qwen Image Text To Image LoRA

- **Model ID:** `wavespeed-ai/qwen-image/text-to-image-lora`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/qwen-image/text-to-image-lora`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/qwen-image-text-to-image-lora

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 1024*1024 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `loras`: array No max 3 items List of LoRAs to apply (maximum 3). loras[].path string Yes - Path to the LoRA model loras[].scale float Yes - 0.0 ~ 4.0 Scale of the LoRA model
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `output_format`: string No jpeg jpeg, png, webp The format of the output image.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Rife

- **Model ID:** `wavespeed-ai/rife`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/rife`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/rife

**Request Parameters**

- `video`: string Yes - The URL of the video to interpolate.

### Sam3 Image

- **Model ID:** `wavespeed-ai/sam3-image`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/sam3-image`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/sam3-image

**Request Parameters**

- `image`: string Yes - URL of the image to segment and analyze
- `prompt`: string No - Text description to guide which objects or regions to segment
- `point_prompts`: array No [] - List of point coordinates to mark specific locations for segmentation (foreground or background)
- `box_prompts`: array No [] - List of bounding boxes to define rectangular regions for segmentation
- `apply_mask`: boolean No true - Whether to overlay the segmentation mask on the original image
- `output_format`: string No png jpeg, png, webp Output image format for the segmented result

### Sam3 Image Rle

- **Model ID:** `wavespeed-ai/sam3-image-rle`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/sam3-image-rle`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/sam3-image-rle

**Request Parameters**

- `image`: string Yes - URL of the image to segment and analyze
- `prompt`: string No - Text description to guide which objects or regions to segment
- `point_prompts`: array No [] - List of point coordinates to mark specific locations for segmentation (foreground or background)
- `box_prompts`: array No [] - List of bounding boxes to define rectangular regions for segmentation
- `apply_mask`: boolean No true - Whether to overlay the segmentation mask on the original image
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Scail

- **Model ID:** `wavespeed-ai/scail`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/scail`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/scail

**Request Parameters**

- `image`: string Yes - The image for generating the output.
- `video`: string Yes - The video for generating the output.
- `prompt`: string No - The positive prompt for the generation.
- `resolution`: string No 480p 480p, 720p The resolution of the output video.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Soulx Flashhead

- **Model ID:** `wavespeed-ai/soulx-flashhead`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/soulx-flashhead`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/soulx-flashhead

**Request Parameters**

- `image`: string Yes - Portrait image to generate talking head video from
- `audio`: string Yes - - Audio file to drive the facial animation and lip-sync (up to 30 minutes)
- `resolution`: string No 720p 480p, 720p Output video resolution (480p or 720p)
- `seed`: integer No -1 -1 ~ 2147483647 Random seed for reproducible generation (default: -1)

### Steady Dancer

- **Model ID:** `wavespeed-ai/steady-dancer`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/steady-dancer`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/steady-dancer

**Request Parameters**

- `image`: string Yes - The image for generating the output.
- `video`: string Yes - The video for generating the output.
- `prompt`: string No - The positive prompt for the generation.
- `resolution`: string No 480p 480p, 720p The resolution of the output video.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Step1x Edit

- **Model ID:** `wavespeed-ai/step1x-edit`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/step1x-edit`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/step1x-edit

**Request Parameters**

- `image`: string Yes - The image URL to generate an image from. Needs to match the dimensions of the mask.
- `prompt`: string Yes - The positive prompt for the generation.
- `negative_prompt`: string No - The negative prompt for the generation.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation.
- `guidance_scale`: number No 4 0 ~ 20 The guidance scale to use for the generation.
- `num_inference_steps`: integer No 30 1 ~ 50 The number of inference steps to perform.

### Think Sound

- **Model ID:** `wavespeed-ai/think-sound`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/think-sound`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/think-sound

**Request Parameters**

- `video`: string Yes - The video for generating the output.
- `prompt`: string No - The positive prompt for the generation.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Uno

- **Model ID:** `wavespeed-ai/uno`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/uno`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/uno

**Request Parameters**

- `images`: array Yes [] - URL of images to use while generating the image.
- `image_size`: string No square_hd square_hd, square, portrait_4_3, portrait_16_9, landscape_4_3, landscape_16_9 The aspect ratio of the generated media.
- `prompt`: string Yes - The positive prompt for the generation.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation.
- `num_images`: integer No 1 1 ~ 4 The number of images to generate.
- `num_inference_steps`: integer No 28 1 ~ 50 The number of inference steps to perform.
- `guidance_scale`: number No 3.5 1 ~ 20 The guidance scale to use for the generation.
- `output_format`: string No jpeg jpeg, png The format of the output image.

### Wan 2.1 14b Vace

- **Model ID:** `wavespeed-ai/wan-2.1-14b-vace`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/wan-2.1-14b-vace`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/wan-2.1-14b-vace

**Request Parameters**

- `prompt`: string Yes -
- `images`: array No [] - URL of ref images to use while generating the video.
- `video`: string No - The video for generating the output.
- `task`: string No depth depth, pose, face, inpainting, none Extract control information from the provided video to guide video generation.
- `negative_prompt`: string No - The negative prompt for the generation.
- `mask_video`: string No - - URL of the mask video.
- `mask_image`: string No - URL of the mask image.
- `first_image`: string No - - URL of the first image.
- `last_image`: string No - - URL of the last image.
- `duration`: integer No 5 5 ~ 10 The duration of the generated media in seconds.
- `size`: string No 832*480 832*480, 480*832, 1280*720, 720*1280 The size of the generated media in pixels (width*height).
- `num_inference_steps`: integer No 30 1 ~ 40 The number of inference steps to perform.
- `guidance_scale`: number No 5 0.0 ~ 20.0 The guidance scale to use for the generation.
- `flow_shift`: number No 16 0.0 ~ 30.0 The shift value for the timestep schedule for flow matching.
- `context_scale`: number No 1 0.0 ~ 2.0 Controls how close you want the model to stick to the reference context.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Wan 2.1 Ditto

- **Model ID:** `wavespeed-ai/wan-2.1/ditto`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/wan-2.1/ditto`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/wan-2.1-ditto

**Request Parameters**

- `video`: string Yes - URL to the video to use as the input for the generation.
- `prompt`: string Yes RealDomain, FireScene, Steampunk, JapaneseAnime, PencilSketch, PixelArt, Claymation, Ukiyo-e, Renaissance, VanGogh, Cyberpunk, Watercolor, ComicBook, ChildrenBook, Charcoal, RickAndMorty, SpiritedAway, MoeAnime, Pixar, GoldenAge,
- `resolution`: string No 480p 480p, 720p The resolution of the output video.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Wan 2.1 Mocha

- **Model ID:** `wavespeed-ai/wan-2.1/mocha`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/wan-2.1/mocha`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/wan-2.1-mocha

**Request Parameters**

- `image`: string Yes - The image for generating the output.
- `video`: string Yes - The video for generating the output.
- `prompt`: string No - The positive prompt for the generation.
- `resolution`: string No 480p 480p, 720p The resolution of the output video.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Wan 2.1 Multitalk

- **Model ID:** `wavespeed-ai/wan-2.1/multitalk`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/wan-2.1/multitalk`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/wan-2.1-multitalk

**Request Parameters**

- `image`: string Yes - The image for generating the output.
- `audio`: string Yes - - The audio for generating the output.
- `prompt`: string No - The positive prompt for the generation.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Wan 2.1 Synthetic To Real Ditto

- **Model ID:** `wavespeed-ai/wan-2.1/synthetic-to-real-ditto`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/wan-2.1/synthetic-to-real-ditto`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/wan-2.1-synthetic-to-real-ditto

**Request Parameters**

- `video`: string Yes - URL to the video to use as the input for the generation.
- `resolution`: string No 480p 480p, 720p The resolution of the output video.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Wan 2.1 Text To Image

- **Model ID:** `wavespeed-ai/wan-2.1/text-to-image`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/wan-2.1/text-to-image`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/wan-2.1-text-to-image

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `image`: string No - The image to generate an image from (optional).
- `strength`: number No 0.6 0.00 ~ 1.00 Strength indicates extent to transform the reference image.
- `size`: string No 1024*1024 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `output_format`: string No jpeg jpeg, png, webp The format of the output image.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Wan 2.2 Image To Image

- **Model ID:** `wavespeed-ai/wan-2.1/text-to-image`
- **Operation:** `edit_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/wan-2.1/text-to-image`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/wan-2.2-image-to-image

**Request Parameters**

- `image`: string Yes - The image to generate an image from (optional).
- `prompt`: string Yes - The positive prompt for the generation.
- `strength`: number No 0.6 0.00 ~ 1.00 Strength indicates extent to transform the reference image.
- `size`: string No 1024*1024 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `output_format`: string No jpeg jpeg, png, webp The format of the output image.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Wan 2.1 Text To Image LoRA

- **Model ID:** `wavespeed-ai/wan-2.1/text-to-image-lora`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/wan-2.1/text-to-image-lora`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/wan-2.1-text-to-image-lora

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `image`: string No - The image to generate an image from (optional).
- `strength`: number No 0.6 0.00 ~ 1.00 Strength indicates extent to transform the reference image.
- `loras`: array No max 3 items List of LoRAs to apply (maximum 3). loras[].path string Yes - Path to the LoRA model loras[].scale float Yes - 0.0 ~ 4.0 Scale of the LoRA model
- `size`: string No 1024*1024 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `output_format`: string No jpeg jpeg, png, webp The format of the output image.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Wan 2.2 Animate

- **Model ID:** `wavespeed-ai/wan-2.2/animate`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/wan-2.2/animate`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/wan-2.2-animate

**Request Parameters**

- `image`: string Yes - The image for generating the output.
- `video`: string Yes - The video for generating the output.
- `prompt`: string No - The positive prompt for the generation.
- `mode`: string No animate animate, replace The mode of the generation. Animate Mode: animate the character in input image with movements from the input video. Replace Mode: replace the character in input video with the character in input image.
- `resolution`: string No 480p 480p, 720p The resolution of the output video.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Wan 2.2 Fun Control

- **Model ID:** `wavespeed-ai/wan-2.2/fun-control`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/wan-2.2/fun-control`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/wan-2.2-fun-control

**Request Parameters**

- `image`: string Yes - The image for generating the output.
- `video`: string Yes - The video for generating the output.
- `prompt`: string No - The positive prompt for the generation.
- `resolution`: string No 480p 480p, 720p The resolution of the output video.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Wan 2.2 Text To Image LoRA

- **Model ID:** `wavespeed-ai/wan-2.2/text-to-image-lora`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/wan-2.2/text-to-image-lora`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/wan-2.2-text-to-image-lora

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 1024*1024 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `loras`: array No max 3 items List of LoRAs to apply (max 3). loras[].path string Yes - Path to the LoRA model loras[].scale float Yes - 0.0 ~ 4.0 Scale of the LoRA model
- `high_noise_loras`: array No - - List of high noise LoRAs to apply (max 3).
- `low_noise_loras`: array No - - List of low noise LoRAs to apply (max 3).
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `output_format`: string No jpeg jpeg, png, webp The format of the output image.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Wan 2.2 Text To Image Realism

- **Model ID:** `wavespeed-ai/wan-2.2/text-to-image-realism`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/wan-2.2/text-to-image-realism`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/wan-2.2-text-to-image-realism

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 1024*1024 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `output_format`: string No jpeg jpeg, png, webp The format of the output image.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Wan Flf2v

- **Model ID:** `wavespeed-ai/wan-flf2v`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/wan-flf2v`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/wan-flf2v

**Request Parameters**

- `first_image`: string Yes - - URL of the starting image.
- `last_image`: string Yes - - URL of the ending image.
- `prompt`: string No -
- `negative_prompt`: string No - The negative prompt for the generation.
- `duration`: integer No 5 5 ~ 10 The duration of the generated media in seconds.
- `size`: string No 832*480 832*480, 480*832, 1280*720, 720*1280 The size of the generated media in pixels (width*height).
- `num_inference_steps`: integer No 30 1 ~ 40 The number of inference steps to perform.
- `guidance_scale`: number No 5 0.00 ~ 20.00 The guidance scale to use for the generation.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Z Image Turbo Controlnet

- **Model ID:** `wavespeed-ai/z-image-turbo/controlnet`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/z-image-turbo/controlnet`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/z-image-turbo-controlnet

**Request Parameters**

- `image`: string Yes - Reference image URL for ControlNet to extract structural guidance from.
- `prompt`: string Yes - Text description of the image you want to generate.
- `mode`: string No depth depth, canny, pose, none ControlNet mode: 'depth' for depth map guidance, 'canny' for edge detection, 'pose' for human pose estimation, 'none' for no control.
- `size`: string No 1024*1024 256 ~ 1536 per dimension Output image size in pixels (width*height).
- `strength`: number No 1 0.00 ~ 1.00 Controls how strongly the ControlNet guidance affects the output (0-1). Higher values follow the control signal more strictly.
- `seed`: integer No -1 -1 ~ 2147483647 Random seed for reproducible generation. Use -1 for random seed.
- `output_format`: string No jpeg jpeg, png, webp Output image format.
- `enable_sync_mode`: boolean No false - If true, waits for generation to complete before returning. API only.
- `enable_base64_output`: boolean No false - If true, returns BASE64 encoded image instead of URL. API only.

### Z Image Turbo Image To Image

- **Model ID:** `wavespeed-ai/z-image-turbo/image-to-image`
- **Operation:** `edit_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/z-image-turbo/image-to-image`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/z-image-turbo-image-to-image

**Request Parameters**

- `image`: string Yes - Reference image URL to guide the generation style or composition.
- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 1024*1024 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `strength`: number No 0.6 0.00 ~ 1.00 Controls the strength of the transformation. Higher values produce outputs more different from the input image.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `output_format`: string No jpeg jpeg, png, webp The format of the output image.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Z Image Turbo Image To Image LoRA

- **Model ID:** `wavespeed-ai/z-image-turbo/image-to-image-lora`
- **Operation:** `edit_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/z-image-turbo/image-to-image-lora`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/z-image-turbo-image-to-image-lora

**Request Parameters**

- `image`: string Yes - Reference image URL to guide the generation style or composition.
- `prompt`: string Yes - The positive prompt for the generation.
- `loras`: array No max 3 items List of LoRAs to apply (maximum 3). loras[].path string Yes - Path to the LoRA model loras[].scale float Yes - 0.0 ~ 4.0 Scale of the LoRA model
- `size`: string No 1024*1024 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `strength`: number No 0.6 0.00 ~ 1.00 Controls the strength of the transformation. Higher values produce outputs more different from the input image.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `output_format`: string No jpeg jpeg, png, webp The format of the output image.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Z Image Base

- **Model ID:** `wavespeed-ai/z-image/base`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/z-image/base`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/z-image-base

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `negative_prompt`: string No - The negative prompt for the generation. Describes what you don't want in the image.
- `image`: string No - URL of the reference image to guide the generation.
- `size`: string No 1024*1024 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `strength`: number No 0.6 0.00 ~ 1.00 Controls the strength of the transformation. Higher values produce outputs more different from the input image.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `output_format`: string No jpeg jpeg, png, webp The format of the output image.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Z Image Base LoRA

- **Model ID:** `wavespeed-ai/z-image/base-lora`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/z-image/base-lora`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/z-image-base-lora

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `negative_prompt`: string No - The negative prompt for the generation. Describes what you don't want in the image.
- `image`: string No - URL of the reference image to guide the generation.
- `loras`: array No max 3 items List of LoRAs to apply (maximum 3). loras[].path string Yes - Path to the LoRA model loras[].scale float Yes - 0.0 ~ 4.0 Scale of the LoRA model
- `size`: string No 1024*1024 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `strength`: number No 0.6 0.00 ~ 1.00 Controls the strength of the transformation. Higher values produce outputs more different from the input image.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `output_format`: string No jpeg jpeg, png, webp The format of the output image.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Z Image Turbo

- **Model ID:** `wavespeed-ai/z-image/turbo`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/z-image/turbo`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/z-image-turbo

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `image`: string No - URL of the reference image to guide the generation.
- `size`: string No 1024*1024 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `strength`: number No 0.6 0.00 ~ 1.00 Controls the strength of the transformation. Higher values produce outputs more different from the input image.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `output_format`: string No jpeg jpeg, png, webp The format of the output image.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Z Image Turbo LoRA

- **Model ID:** `wavespeed-ai/z-image/turbo-lora`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/z-image/turbo-lora`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/z-image-turbo-lora

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `loras`: array No max 3 items List of LoRAs to apply (maximum 3). loras[].path string Yes - Path to the LoRA model loras[].scale float Yes - 0.0 ~ 4.0 Scale of the LoRA model
- `size`: string No 1024*1024 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `output_format`: string No jpeg jpeg, png, webp The format of the output image.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### X AI Grok 2 Image

- **Model ID:** `x-ai/grok-2-image`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/x-ai/grok-2-image`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/x-ai/x-ai-grok-2-image

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `num_images`: integer No 1 1 ~ 10 Number of images to be generated.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### X AI Grok Imagine Image Edit

- **Model ID:** `x-ai/grok-imagine-image/edit`
- **Operation:** `edit_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/x-ai/grok-imagine-image/edit`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/x-ai/x-ai-grok-imagine-image-edit

**Request Parameters**

- `image`: string Yes - The source image to edit.
- `prompt`: string Yes - The prompt to edit the image with.

### X AI Grok Imagine Image Text To Image

- **Model ID:** `x-ai/grok-imagine-image/text-to-image`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/x-ai/grok-imagine-image/text-to-image`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/x-ai/x-ai-grok-imagine-image-text-to-image

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `aspect_ratio`: string No - 2:1, 20:9, 16:9, 4:3, 3:2, 1:1, 2:3, 3:4, 9:16, 9:20, 1:2 Aspect ratio of the generated image.
- `output_format`: string No jpeg jpeg, png Output image format.

### Z AI Cogview 4

- **Model ID:** `z-ai/cogview-4`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/z-ai/cogview-4`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/z-ai/z-ai-cogview-4

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 1024*1024 1024*1024, 768*1344, 864*1152, 1344*768, 1152*864, 1440*720, 720*1440 The quality of the generated image
- `quality`: string No hd standard, hd The quality of the generated image
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Z AI Glm Image Edit

- **Model ID:** `z-ai/glm-image/edit`
- **Operation:** `edit_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/z-ai/glm-image/edit`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/z-ai/z-ai-glm-image-edit

**Request Parameters**

- `images`: array Yes [] 1 ~ 4 items URL(s) of condition image(s) for image-to-image generation. Supports up to 4 URLs.
- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No - 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `output_format`: string No jpeg jpeg, png, webp The format of the output image.
- `enable_prompt_expansion`: boolean No false - Enhance prompt using LLM for better results.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Z AI Glm Image Text To Image

- **Model ID:** `z-ai/glm-image/text-to-image`
- **Operation:** `text_to_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/z-ai/glm-image/text-to-image`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/z-ai/z-ai-glm-image-text-to-image

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 1024*1024 256 ~ 1536 per dimension The size of the generated media in pixels (width*height).
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `output_format`: string No jpeg jpeg, png, webp The format of the output image.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.


## Category: lipsync

### Bytedance Avatar Omni Human

- **Model ID:** `bytedance/avatar-omni-human`
- **Operation:** `lipsync`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bytedance/avatar-omni-human`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bytedance/bytedance-avatar-omni-human

**Request Parameters**

- `image`: string Yes - The portrait image to animate, can be a URL or base64 encoded image. Better results with clear, front-facing portraits with good lighting.
- `audio`: string Yes - - Optional background audio for the generated video, can be a URL or base64 encoded audio file.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Bytedance Avatar Omni Human 1.5

- **Model ID:** `bytedance/avatar-omni-human-1.5`
- **Operation:** `lipsync`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bytedance/avatar-omni-human-1.5`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bytedance/bytedance-avatar-omni-human-1.5

**Request Parameters**

- `image`: string Yes - The portrait image to animate, can be a URL or base64 encoded image. Better results with clear, front-facing portraits with good lighting.
- `audio`: string Yes - - Optional background audio for the generated video, can be a URL or base64 encoded audio file.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Bytedance Lipsync Audio To Video

- **Model ID:** `bytedance/lipsync/audio-to-video`
- **Operation:** `lipsync`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bytedance/lipsync/audio-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bytedance/bytedance-lipsync-audio-to-video

**Request Parameters**

- `audio`: string Yes - - The URL pointing to the audio file that will be used for generating synchronized lip movements.
- `video`: string Yes - The URL of the video file for generating synchronized lip movements.

### Kwaivgi Kling Lipsync Audio To Video

- **Model ID:** `kwaivgi/kling-lipsync/audio-to-video`
- **Operation:** `lipsync`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/kwaivgi/kling-lipsync/audio-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/kwaivgi/kwaivgi-kling-lipsync-audio-to-video

**Request Parameters**

- `audio`: string Yes - - The URL pointing to the audio file that will be used for generating synchronized lip movements. Supported audio file formats: .mp3/.wav/.m4a/.aac, with a maximum file size of 5MB.
- `video`: string Yes - The URL of the video file for generating synchronized lip movements. Video files support .mp4/.mov, file size does not exceed 100MB, video length does not exceed 10s and is not shorter than 2s, only 720p and 1080p are supported

### Kwaivgi Kling Lipsync Text To Video

- **Model ID:** `kwaivgi/kling-lipsync/text-to-video`
- **Operation:** `lipsync`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/kwaivgi/kling-lipsync/text-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/kwaivgi/kwaivgi-kling-lipsync-text-to-video

**Request Parameters**

- `video`: string Yes - The URL of the video file for generating synchronized lip movements. Video files support .mp4/.mov, file size does not exceed 100MB, video length does not exceed 10s and is not shorter than 2s, only 720p and 1080p are supported
- `text`: string Yes - - Text Content for Lip-Sync Video Generation. Max 120 characters.
- `voice_id`: string Yes genshin_klee2 genshin_vindi2, zhinen_xuesheng, AOT, ai_shatang, genshin_klee2, genshin_kirara, ai_kaiya, oversea_male1, ai_chenjiahao_712, girlfriend_4_speech02, chat1_female_new-3, chat_0407_5-1, cartoon-boy-07, uk_boy1, cartoon
- `voice_language`: string No en zh, en The voice language corresponding to the Voice ID
- `voice_speed`: number No 1 0.8 ~ 2.0 Speech rate for Text to Video generation

### Kwaivgi Kling V1 AI Avatar Pro

- **Model ID:** `kwaivgi/kling-v1-ai-avatar-pro`
- **Operation:** `lipsync`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/kwaivgi/kling-v1-ai-avatar-pro`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/kwaivgi/kwaivgi-kling-v1-ai-avatar-pro

**Request Parameters**

- `image`: string Yes - The image for generating the output.
- `audio`: string Yes - - The audio for generating the output.
- `prompt`: string No - The positive prompt for the generation.

### Kwaivgi Kling V1 AI Avatar Standard

- **Model ID:** `kwaivgi/kling-v1-ai-avatar-standard`
- **Operation:** `lipsync`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/kwaivgi/kling-v1-ai-avatar-standard`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/kwaivgi/kwaivgi-kling-v1-ai-avatar-standard

**Request Parameters**

- `image`: string Yes - The image for generating the output.
- `audio`: string Yes - - The audio for generating the output.
- `prompt`: string No - The positive prompt for the generation.

### Kwaivgi Kling V2 AI Avatar Pro

- **Model ID:** `kwaivgi/kling-v2-ai-avatar-pro`
- **Operation:** `lipsync`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/kwaivgi/kling-v2-ai-avatar-pro`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/kwaivgi/kwaivgi-kling-v2-ai-avatar-pro

**Request Parameters**

- `image`: string Yes - The image for generating the output.
- `audio`: string Yes - - The audio for generating the output.
- `prompt`: string No - The positive prompt for the generation.

### Kwaivgi Kling V2 AI Avatar Standard

- **Model ID:** `kwaivgi/kling-v2-ai-avatar-standard`
- **Operation:** `lipsync`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/kwaivgi/kling-v2-ai-avatar-standard`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/kwaivgi/kwaivgi-kling-v2-ai-avatar-standard

**Request Parameters**

- `image`: string Yes - The image for generating the output.
- `audio`: string Yes - - The audio for generating the output.
- `prompt`: string No - The positive prompt for the generation.

### Pixverse Lipsync

- **Model ID:** `pixverse/lipsync`
- **Operation:** `lipsync`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/pixverse/lipsync`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/pixverse/pixverse-lipsync

**Request Parameters**

- `audio`: string Yes - - The audio for generating the output.
- `video`: string Yes - The video for generating the output.

### Sync Lipsync 1.9.0 Beta

- **Model ID:** `sync/lipsync-1.9.0-beta`
- **Operation:** `lipsync`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/sync/lipsync-1.9.0-beta`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/sync/sync-lipsync-1.9.0-beta

**Request Parameters**

- `video`: string Yes - The video to be used for generation
- `audio`: string Yes - - The audio to be used for generation
- `sync_mode`: string No cut_off bounce, loop, cut_off, silence, remap Defines how to handle duration mismatches between video and audio inputs. See the Media Content Tips guide https://docs.sync.so/compatibility-and-tips/media-content-tips#sync-mode-opti

### Sync Lipsync 2

- **Model ID:** `sync/lipsync-2`
- **Operation:** `lipsync`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/sync/lipsync-2`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/sync/sync-lipsync-2

**Request Parameters**

- `video`: string Yes - The video to be used for generation
- `audio`: string Yes - - The audio to be used for generation
- `sync_mode`: string No cut_off bounce, loop, cut_off, silence, remap Defines how to handle duration mismatches between video and audio inputs. See the Media Content Tips guide https://docs.sync.so/compatibility-and-tips/media-content-tips#sync-mode-opti

### Sync Lipsync 2 Pro

- **Model ID:** `sync/lipsync-2-pro`
- **Operation:** `lipsync`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/sync/lipsync-2-pro`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/sync/sync-lipsync-2-pro

**Request Parameters**

- `video`: string Yes - The video to be used for generation
- `audio`: string Yes - - The audio to be used for generation
- `sync_mode`: string No cut_off bounce, loop, cut_off, silence, remap Defines how to handle duration mismatches between video and audio inputs. See the Media Content Tips guide https://docs.sync.so/compatibility-and-tips/media-content-tips#sync-mode-opti

### Sync Lipsync 3

- **Model ID:** `sync/lipsync-3`
- **Operation:** `lipsync`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/sync/lipsync-3`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/sync/sync-lipsync-3

**Request Parameters**

- `video`: string Yes - The video to be used for generation
- `audio`: string Yes - - The audio to be used for generation
- `sync_mode`: string No cut_off bounce, loop, cut_off, silence, remap Defines how to handle duration mismatches between video and audio inputs. See the Media Content Tips guide https://docs.sync.so/compatibility-and-tips/media-content-tips#sync-mode-opti

### Veed Lipsync

- **Model ID:** `veed/lipsync`
- **Operation:** `lipsync`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/veed/lipsync`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/veed/veed-lipsync

**Request Parameters**

- `audio`: string Yes - - The audio for generating the output.
- `video`: string Yes - The video for generating the output.

### Hunyuan Avatar

- **Model ID:** `wavespeed-ai/hunyuan-avatar`
- **Operation:** `lipsync`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/hunyuan-avatar`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/hunyuan-avatar

**Request Parameters**

- `image`: string Yes - The image for generating the output.
- `audio`: string Yes - - The audio for generating the output.
- `prompt`: string No - The positive prompt for the generation.
- `resolution`: string No 480p 480p, 720p The resolution of the output video.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Longcat Avatar

- **Model ID:** `wavespeed-ai/longcat-avatar`
- **Operation:** `lipsync`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/longcat-avatar`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/longcat-avatar

**Request Parameters**

- `image`: string Yes - The image for generating the output.
- `audio`: string Yes - - The audio for generating the output.
- `prompt`: string No - The positive prompt for the generation.
- `resolution`: string No 480p 480p, 720p The resolution of the output video.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Ltx 2 19b Lipsync

- **Model ID:** `wavespeed-ai/ltx-2-19b/lipsync`
- **Operation:** `lipsync`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/ltx-2-19b/lipsync`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/ltx-2-19b-lipsync

**Request Parameters**

- `audio`: string Yes - - The audio file URL for lip-sync generation. Duration determines video length (5-20 seconds max).
- `image`: string No - The reference image for the generation. Optional - if not provided, a default portrait will be used.
- `prompt`: string No - Optional text prompt to guide the generation style and motion.
- `resolution`: string No 720p 480p, 720p, 1080p Video resolution.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Ltx 2.3 Lipsync

- **Model ID:** `wavespeed-ai/ltx-2.3/lipsync`
- **Operation:** `lipsync`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/ltx-2.3/lipsync`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/ltx-2.3-lipsync

**Request Parameters**

- `audio`: string Yes - - The audio file URL for lip-sync generation. Duration determines video length (5-20 seconds max).
- `image`: string No - The reference image for the generation. Optional - if not provided, a default portrait will be used.
- `prompt`: string No - Optional text prompt to guide the generation style and motion.
- `resolution`: string No 720p 480p, 720p, 1080p Video resolution.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Skyreels V3 Talking Avatar

- **Model ID:** `wavespeed-ai/skyreels-v3/talking-avatar`
- **Operation:** `lipsync`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/skyreels-v3/talking-avatar`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/skyreels-v3-talking-avatar

**Request Parameters**

- `prompt`: string No - Text description of the avatar behavior, emotion, and camera style (e.g., 'confident and joyful, static shot')
- `image`: string Yes - Portrait image for avatar generation (supports jpg, png, gif, bmp)
- `audio`: string Yes - - Audio file to drive the talking avatar with lip sync (supports mp3, wav, up to 15 seconds)
- `resolution`: string No 720p 480p, 720p Output video resolution (480p or 720p)
- `seed`: integer No -1 -1 ~ 2147483647 Random seed for reproducible generation

### Wan 2.2 Speech To Video

- **Model ID:** `wavespeed-ai/wan-2.2/speech-to-video`
- **Operation:** `speech_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/wan-2.2/speech-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/wan-2.2-speech-to-video

**Request Parameters**

- `image`: string Yes - The image for generating the output.
- `audio`: string Yes - - The audio for generating the output.
- `prompt`: string No - The positive prompt for the generation.
- `resolution`: string No 480p 480p, 720p The resolution of the output video.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.


## Category: trainer

### Flux Dev LoRA Trainer

- **Model ID:** `wavespeed-ai/flux-dev-lora-trainer`
- **Operation:** `train`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/flux-dev-lora-trainer`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/flux-dev-lora-trainer

**Request Parameters**

- `data`: string Yes - - URL to zip archive with images. Try to use at least 4 images in general the more the better. In addition to images the archive can contain text files with captions. Each text file should have the same name as the image file i
- `trigger_word`: string No p3r5on - Trigger word to be used in the captions. If None, a trigger word will not be used. If no captions are provide the trigger_word will be used instead of captions. If captions are the trigger word will not be used.
- `steps`: integer No 1000 500 ~ 10000 Number of steps to train the LoRA on.
- `learning_rate`: number No 0.0004 0.00000 ~ 1.00000
- `lora_rank`: integer No 16 1 ~ 64

### Flux Dev LoRA Trainer Turbo

- **Model ID:** `wavespeed-ai/flux-dev-lora-trainer`
- **Operation:** `train`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/flux-dev-lora-trainer`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/flux-dev-lora-trainer-turbo

**Request Parameters**

- `data`: string Yes - - URL to zip archive with images. Try to use at least 4 images in general the more the better. In addition to images the archive can contain text files with captions. Each text file should have the same name as the image file i
- `trigger_word`: string No p3r5on - Trigger word to be used in the captions. If None, a trigger word will not be used. If no captions are provide the trigger_word will be used instead of captions. If captions are the trigger word will not be used.
- `steps`: integer No 1000 1000 ~ 10000 Number of steps to train the LoRA on.
- `learning_rate`: number No 0.0004 0.00000 ~ 1.00000
- `lora_rank`: integer No 16 1 ~ 64

### Ltx 2 19b Ic LoRA Trainer

- **Model ID:** `wavespeed-ai/ltx-2-19b/ic-lora-trainer`
- **Operation:** `train`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/ltx-2-19b/ic-lora-trainer`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/ltx-2-19b-ic-lora-trainer

**Request Parameters**

- `data`: string Yes - - Upload a zip file containing paired reference and target videos for IC-LoRA training. Reference videos must be named with '_ref.mp4' suffix (e.g., 'video1_ref.mp4' pairs with 'video1.mp4'). Reference and target videos must ha
- `trigger_word`: string No p3r5on - The phrase that will trigger the model to generate a video.
- `steps`: integer No 500 100 ~ 20000 Number of steps to train the LoRA on.
- `learning_rate`: number No 0.0002 0.00000 ~ 1.00000
- `lora_rank`: integer No 32 1 ~ 128

### Ltx 2 19b Video LoRA Trainer

- **Model ID:** `wavespeed-ai/ltx-2-19b/video-lora-trainer`
- **Operation:** `train`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/ltx-2-19b/video-lora-trainer`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/ltx-2-19b-video-lora-trainer

**Request Parameters**

- `data`: string Yes - - Upload a zip file containing videos with optional audio latents for audio-video LoRA training. Each text file should have the same name as the video file it corresponds to for captions.
- `trigger_word`: string No p3r5on - The phrase that will trigger the model to generate a video.
- `steps`: integer No 500 100 ~ 20000 Number of steps to train the LoRA on.
- `learning_rate`: number No 0.0002 0.00000 ~ 1.00000
- `lora_rank`: integer No 32 1 ~ 128

### Qwen Image 2512 LoRA Trainer

- **Model ID:** `wavespeed-ai/qwen-image-2512-lora-trainer`
- **Operation:** `train`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/qwen-image-2512-lora-trainer`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/qwen-image-2512-lora-trainer

**Request Parameters**

- `data`: string Yes - - URL to zip archive with images. Try to use at least 4 images in general the more the better. In addition to images the archive can contain text files with captions. Each text file should have the same name as the image file i
- `trigger_word`: string No p3r5on - Trigger word to be used in the captions. If None, a trigger word will not be used. If no captions are provide the trigger_word will be used instead of captions. If captions are the trigger word will not be used.
- `steps`: integer No 1000 500 ~ 10000 Number of steps to train the LoRA on.
- `learning_rate`: number No 0.0004 0.00000 ~ 1.00000
- `lora_rank`: integer No 16 1 ~ 64

### Qwen Image LoRA Trainer

- **Model ID:** `wavespeed-ai/qwen-image-lora-trainer`
- **Operation:** `train`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/qwen-image-lora-trainer`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/qwen-image-lora-trainer

**Request Parameters**

- `data`: string Yes - - URL to zip archive with images. Try to use at least 4 images in general the more the better. In addition to images the archive can contain text files with captions. Each text file should have the same name as the image file i
- `trigger_word`: string No p3r5on - Trigger word to be used in the captions. If None, a trigger word will not be used. If no captions are provide the trigger_word will be used instead of captions. If captions are the trigger word will not be used.
- `steps`: integer No 1000 500 ~ 10000 Number of steps to train the LoRA on.
- `learning_rate`: number No 0.0004 0.00000 ~ 1.00000
- `lora_rank`: integer No 16 1 ~ 64

### Wan 2.1 14b LoRA Trainer

- **Model ID:** `wavespeed-ai/wan-2.1-14b-lora-trainer`
- **Operation:** `train`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/wan-2.1-14b-lora-trainer`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/wan-2.1-14b-lora-trainer

**Request Parameters**

- `data`: string Yes - - To train a WAN Lora, you need at least 10 face images to achieve good results.you can check out our default image dataset.
- `trigger_word`: string No p3r5on - The phrase that will trigger the model to generate an video.
- `steps`: integer No 2000 1000 ~ 10000 Number of steps to train the LoRA on.
- `learning_rate`: number No 0.0001 0.00000 ~ 1.00000
- `lora_rank`: integer No 32 1 ~ 64

### Wan 2.2 Image LoRA Trainer

- **Model ID:** `wavespeed-ai/wan-2.2-image-lora-trainer`
- **Operation:** `train`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/wan-2.2-image-lora-trainer`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/wan-2.2-image-lora-trainer

**Request Parameters**

- `data`: string Yes - - To train a WAN T2V LoRA, you need to upload a zip file containing at least 10 images. In addition to images the archive can contain text files with captions. Each text file should have the same name as the image file it corre
- `trigger_word`: string No p3r5on - The phrase that will trigger the model to generate an video.
- `steps`: integer No 1000 1000 ~ 10000 Number of steps to train the LoRA on.
- `learning_rate`: number No 0.0002 0.00000 ~ 1.00000
- `lora_rank`: integer No 32 1 ~ 128

### Z Image LoRA Trainer

- **Model ID:** `wavespeed-ai/z-image-lora-trainer`
- **Operation:** `train`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/z-image-lora-trainer`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/z-image-lora-trainer

**Request Parameters**

- `data`: string Yes - - URL to zip archive with images. Try to use at least 4 images in general the more the better. In addition to images the archive can contain text files with captions. Each text file should have the same name as the image file i
- `trigger_word`: string No p3r5on - Trigger word to be used in the captions. If None, a trigger word will not be used. If no captions are provide the trigger_word will be used instead of captions. If captions are the trigger word will not be used.
- `steps`: integer No 1000 500 ~ 10000 Number of steps to train the LoRA on.
- `learning_rate`: number No 0.0001 0.00000 ~ 1.00000
- `lora_rank`: integer No 16 1 ~ 64

### Z Image Base LoRA Trainer

- **Model ID:** `wavespeed-ai/z-image/base-lora-trainer`
- **Operation:** `train`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/z-image/base-lora-trainer`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/z-image-base-lora-trainer

**Request Parameters**

- `data`: string Yes - - URL to zip archive with images. Try to use at least 4 images in general the more the better. In addition to images the archive can contain text files with captions. Each text file should have the same name as the image file i
- `trigger_word`: string No p3r5on - Trigger word to be used in the captions. If None, a trigger word will not be used. If no captions are provided the trigger_word will be used instead of captions.
- `steps`: integer No 1000 500 ~ 10000 Number of steps to train the LoRA on.
- `learning_rate`: number No 0.0001 0.00000 ~ 1.00000
- `lora_rank`: integer No 16 1 ~ 64


## Category: try_on

### AI Clothes Changer

- **Model ID:** `wavespeed-ai/ai-clothes-changer`
- **Operation:** `try_on`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/ai-clothes-changer`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/ai-clothes-changer

**Request Parameters**

- `image`: string Yes - The URL of the input image.
- `clothes_images`: array Yes - 1 ~ 8 items List of clothing image URLs (up to 10).

### AI Virtual Outfit Tryon

- **Model ID:** `wavespeed-ai/ai-virtual-outfit-tryon`
- **Operation:** `try_on`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/ai-virtual-outfit-tryon`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/ai-virtual-outfit-tryon

**Request Parameters**

- `image`: string Yes - The URL of the person image.
- `clothes_images`: array Yes - 1 ~ 8 items List of clothing image URLs (up to 10).
- `prompt`: string No - Text prompt describing the desired outfit video scene.
- `duration`: integer No 5 5 ~ 15 The duration of the generated video in seconds.


## Category: upscale

### Bria Fibo Video Background Remover

- **Model ID:** `bria/fibo/video-background-remover`
- **Operation:** `remove_background`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bria/fibo/video-background-remover`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bria/bria-fibo-video-background-remover

**Request Parameters**

- `video`: string Yes - Publicly accessible URL of the input video. Max duration 60 seconds, max resolution 16K.
- `background_color`: string No Transparent Transparent, Black, White, Gray, Red, Green, Blue, Yellow, Cyan, Magenta Background color to use. Transparent requires alpha-supported output format.
- `output_container_and_codec`: string No webm_vp9 webm_vp9, mp4_h264, mp4_h265, mov_h265, mov_proresks, mkv_h264, mkv_h265, mkv_vp9, gif, avi_h264 Output container and codec preset.
- `preserve_audio`: boolean No true - Whether to preserve audio in the output video.

### Bria Fibo Video Upscaler

- **Model ID:** `bria/fibo/video-upscaler`
- **Operation:** `upscale`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bria/fibo/video-upscaler`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bria/bria-fibo-video-upscaler

**Request Parameters**

- `video`: string Yes - Publicly accessible URL of the input video. Max duration 60 seconds.
- `target_resolution`: string No 2k 2k, 4k Target resolution: 2k = 2x upscaling, 4k = 4x upscaling.
- `output_container_and_codec`: string No mp4_h264 mp4_h264, mp4_h265, webm_vp9, mov_h265, mov_proresks, mkv_h264, mkv_h265, mkv_vp9, gif Output container and codec preset.

### Bria Remove Background

- **Model ID:** `bria/remove-background`
- **Operation:** `remove_background`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bria/remove-background`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bria/bria-remove-background

**Request Parameters**

- `image`: string Yes - The URL of the image to erase.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Bytedance Video Upscaler

- **Model ID:** `bytedance/video-upscaler`
- **Operation:** `upscale`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bytedance/video-upscaler`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bytedance/bytedance-video-upscaler

**Request Parameters**

- `video`: string Yes - The video to upscale.
- `target_resolution`: string No 1080p 1080p, 2k, 4k The target resolution of the video to upscale.

### Clarity AI Creative Upscaler

- **Model ID:** `clarity-ai/creative-upscaler`
- **Operation:** `upscale`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/clarity-ai/creative-upscaler`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/clarity-ai/clarity-ai-creative-upscaler

**Request Parameters**

- `image`: string Yes - Input image to upscale.
- `target_megapixels`: number No 4 1 ~ 64 Requested output size in megapixels. The backend prepares the input so the output lands near this target. Range: 1-64 MP.
- `prompt`: string No - The prompt for the generation
- `style`: string No default default, portrait, anime style:default, portrait, or anime
- `creativity`: number No - -10.0 ~ 10.0 Negative values stay stricter to the source; positive values add more generated detail.
- `resemblance`: number No - -10 ~ 10 value between -10 and 10
- `dynamic`: number No - -10 ~ 10 value between -10 and 10
- `fractality`: number No - -10 ~ 10 value between -10 and 10

### Clarity AI Crystal Upscaler

- **Model ID:** `clarity-ai/crystal-upscaler`
- **Operation:** `upscale`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/clarity-ai/crystal-upscaler`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/clarity-ai/clarity-ai-crystal-upscaler

**Request Parameters**

- `image`: string Yes - Input image to upscale.
- `target_megapixels`: number No 4 1 ~ 200 Requested output size in megapixels. The backend prepares the input so the output lands near this target. Range: 1-200 MP.
- `creativity`: number No - -10.0 ~ 10.0 Negative values stay stricter to the source; positive values add more generated detail.

### Clarity AI Flux Upscaler

- **Model ID:** `clarity-ai/flux-upscaler`
- **Operation:** `upscale`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/clarity-ai/flux-upscaler`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/clarity-ai/clarity-ai-flux-upscaler

**Request Parameters**

- `image`: string Yes - Input image to upscale.
- `target_megapixels`: number No 4 1 ~ 64 Requested output size in megapixels. The backend prepares the input so the output lands near this target. Range: 1-64 MP.
- `prompt`: string No - The prompt for the generation
- `lora_link`: string No - - The URL of the Lora to use for the generation
- `creativity`: number No - -10.0 ~ 10.0 Negative values stay stricter to the source; positive values add more generated detail.

### Clarity AI Pro Upscaler

- **Model ID:** `clarity-ai/pro-upscaler`
- **Operation:** `upscale`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/clarity-ai/pro-upscaler`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/clarity-ai/clarity-ai-pro-upscaler

**Request Parameters**

- `image`: string Yes - Input image to upscale.
- `target_megapixels`: number No 4 1 ~ 64 Requested output size in megapixels. The backend prepares the input so the output lands near this target. Range: 1-64 MP.
- `creativity`: number No - -10.0 ~ 10.0 Negative values stay stricter to the source; positive values add more generated detail.

### Ideogram AI Remove Background

- **Model ID:** `ideogram-ai/remove-background`
- **Operation:** `remove_background`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/ideogram-ai/remove-background`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/ideogram-ai/ideogram-ai-remove-background

**Request Parameters**

- `image`: string Yes - The image whose background needs to be removed. JPEG, PNG and WebP formats are supported, maximum file size 10MB.

### Recraft AI Recraft Creative Upscale

- **Model ID:** `recraft-ai/recraft-creative-upscale`
- **Operation:** `upscale`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/recraft-ai/recraft-creative-upscale`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/recraft-ai/recraft-ai-recraft-creative-upscale

**Request Parameters**

- `image`: string Yes - The image to edit, can be a URL or base64 encoded image.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Recraft AI Recraft Crisp Upscale

- **Model ID:** `recraft-ai/recraft-crisp-upscale`
- **Operation:** `upscale`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/recraft-ai/recraft-crisp-upscale`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/recraft-ai/recraft-ai-recraft-crisp-upscale

**Request Parameters**

- `image`: string Yes - The image to edit, can be a URL or base64 encoded image.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Runwayml Upscale V1

- **Model ID:** `runwayml/upscale-v1`
- **Operation:** `upscale`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/runwayml/upscale-v1`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/runwayml/runwayml-upscale-v1

**Request Parameters**

- `video`: string Yes - The video to upscale.

### Image Background Remover

- **Model ID:** `wavespeed-ai/image-background-remover`
- **Operation:** `remove_background`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/image-background-remover`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/image-background-remover

**Request Parameters**

- `image`: string Yes - The URL of the image to generate an image from.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Image Upscaler

- **Model ID:** `wavespeed-ai/image-upscaler`
- **Operation:** `upscale`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/image-upscaler`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/image-upscaler

**Request Parameters**

- `image`: string Yes - The URL of the image to generate an image from.
- `target_resolution`: string No 4k 2k, 4k, 8k The target resolution of the generated media.
- `output_format`: string No jpeg jpeg, png, webp The format of the output image.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Ltx 2 19b Video Upscaler

- **Model ID:** `wavespeed-ai/ltx-2-19b/video-upscaler`
- **Operation:** `upscale`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/ltx-2-19b/video-upscaler`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/ltx-2-19b-video-upscaler

**Request Parameters**

- `video`: string Yes - The video to upscale.
- `target_resolution`: string No 1080p 720p, 1080p, 2k, 4k Target resolution to upscale to.

### Real Esrgan

- **Model ID:** `wavespeed-ai/real-esrgan`
- **Operation:** `upscale`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/real-esrgan`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/real-esrgan

**Request Parameters**

- `image`: string Yes - The URL of the image to generate an image from.

### Seedvr2 Image

- **Model ID:** `wavespeed-ai/seedvr2/image`
- **Operation:** `upscale`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/seedvr2/image`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/seedvr2-image

**Request Parameters**

- `image`: string Yes - The URL of the image to generate an image from.
- `target_resolution`: string No 4k 2k, 4k, 8k The target resolution of the generated media.
- `output_format`: string No jpeg jpeg, png, webp The format of the output image.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Seedvr2 Video

- **Model ID:** `wavespeed-ai/seedvr2/video`
- **Operation:** `upscale`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/seedvr2/video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/seedvr2-video

**Request Parameters**

- `video`: string Yes - The video to upscale.
- `target_resolution`: string No 1080p 720p, 1080p, 2k, 4k Target resolution to upscale to.

### Ultimate Image Upscaler

- **Model ID:** `wavespeed-ai/ultimate-image-upscaler`
- **Operation:** `upscale`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/ultimate-image-upscaler`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/ultimate-image-upscaler

**Request Parameters**

- `image`: string Yes - The URL of the image to generate an image from.
- `target_resolution`: string No 4k 2k, 4k, 8k The target resolution of the generated media.
- `output_format`: string No jpeg jpeg, png, webp The format of the output image.
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Ultimate Video Upscaler

- **Model ID:** `wavespeed-ai/ultimate-video-upscaler`
- **Operation:** `upscale`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/ultimate-video-upscaler`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/ultimate-video-upscaler

**Request Parameters**

- `video`: string Yes - The video to upscale.
- `target_resolution`: string No 1080p 720p, 1080p, 2k, 4k Target resolution to upscale to.

### Video Background Remover

- **Model ID:** `wavespeed-ai/video-background-remover`
- **Operation:** `remove_background`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/video-background-remover`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/video-background-remover

**Request Parameters**

- `video`: string Yes - URL of the input video to process for background removal or replacement
- `background_image`: string No - - URL of the background image to replace the original video background

### Video Upscaler

- **Model ID:** `wavespeed-ai/video-upscaler`
- **Operation:** `upscale`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/video-upscaler`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/video-upscaler

**Request Parameters**

- `video`: string Yes - The video to upscale.
- `target_resolution`: string No 1080p 720p, 1080p, 2k, 4k Target resolution to upscale to.

### Video Upscaler Pro

- **Model ID:** `wavespeed-ai/video-upscaler-pro`
- **Operation:** `upscale`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/video-upscaler-pro`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/video-upscaler-pro

**Request Parameters**

- `video`: string Yes - The video to upscale.
- `target_resolution`: string No 1080p 720p, 1080p, 2k, 4k Target resolution to upscale to.


## Category: video

### Akool Video Face Swap

- **Model ID:** `akool/video-face-swap`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/akool/video-face-swap`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/akool/akool-video-face-swap

**Request Parameters**

- `source_image`: array Yes - 1 ~ 5 items Source face image URL to be swapped into the video
- `target_image`: array Yes - 1 ~ 5 items Target face image URL that will be replaced in the video
- `video`: string Yes - Input video URL for face swapping
- `face_enhance`: boolean No false - Whether to enhance face quality after swapping
- `enable_base64_output`: boolean No false - If enabled, the output will be encoded into a BASE64 string instead of a URL. This property is only available through the API.

### Alibaba Happyhorse 1.0 Image To Video

- **Model ID:** `alibaba/happyhorse-1.0/image-to-video`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/alibaba/happyhorse-1.0/image-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/alibaba/alibaba-happyhorse-1.0-image-to-video

**Request Parameters**

- `image`: string Yes - URL of the first-frame image. JPEG / PNG / BMP / WEBP. Min dimension 300px. Aspect ratio between 1:2.5 and 2.5:1. Max 10 MB.
- `prompt`: string Yes - Optional text prompt guiding the animation. Max 2500 characters.
- `resolution`: string No 720p 720p, 1080p Output video resolution.
- `duration`: integer No 5 3 ~ 15 Video length in seconds (3-15).
- `seed`: integer No - -1 ~ 2147483647 Random seed for reproducibility.

### Alibaba Happyhorse 1.0 Reference To Video

- **Model ID:** `alibaba/happyhorse-1.0/reference-to-video`
- **Operation:** `reference_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/alibaba/happyhorse-1.0/reference-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/alibaba/alibaba-happyhorse-1.0-reference-to-video

**Request Parameters**

- `prompt`: string Yes - Text description of the desired scene.
- `images`: array Yes [] 1 ~ 9 items Array of reference image URLs (1-9).
- `resolution`: string No 720p 720p, 1080p Output video resolution.
- `aspect_ratio`: string No 16:9 16:9, 9:16, 1:1, 4:3, 3:4 The aspect ratio of the generated video.
- `duration`: integer No 5 3 ~ 15 Video length in seconds (3-15).
- `seed`: integer No - -1 ~ 2147483647 Random seed for reproducibility.

### Alibaba Happyhorse 1.0 Text To Video

- **Model ID:** `alibaba/happyhorse-1.0/text-to-video`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/alibaba/happyhorse-1.0/text-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/alibaba/alibaba-happyhorse-1.0-text-to-video

**Request Parameters**

- `prompt`: string Yes - Text prompt describing the desired video. Max 2500 characters.
- `aspect_ratio`: string No 16:9 16:9, 9:16, 1:1, 4:3, 3:4 The aspect ratio of the generated video.
- `resolution`: string No 720p 720p, 1080p Output video resolution.
- `duration`: integer No 5 3 ~ 15 Video length in seconds (3-15).
- `seed`: integer No - -1 ~ 2147483647 Random seed for reproducibility.

### Alibaba Happyhorse 1.0 Video Edit

- **Model ID:** `alibaba/happyhorse-1.0/video-edit`
- **Operation:** `video_edit`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/alibaba/happyhorse-1.0/video-edit`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/alibaba/alibaba-happyhorse-1.0-video-edit

**Request Parameters**

- `video`: string Yes - The source video to edit.
- `images`: array No [] - Reference images for video editing (0-9).
- `prompt`: string Yes - Text description of the desired edits.
- `resolution`: string No 720p 720p, 1080p Output video resolution.
- `seed`: integer No - -1 ~ 2147483647 Random seed for reproducibility.

### Alibaba Happyhorse 1.0 Video Extend

- **Model ID:** `alibaba/happyhorse-1.0/video-extend`
- **Operation:** `video_extend`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/alibaba/happyhorse-1.0/video-extend`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/alibaba/alibaba-happyhorse-1.0-video-extend

**Request Parameters**

- `video`: string Yes - The source video to extend.
- `prompt`: string Yes - Text description of the desired continuation.
- `resolution`: string No 720p 720p, 1080p Output video resolution.
- `duration`: integer No 5 3 ~ 15 Total duration of the final output video in seconds (3-15).
- `seed`: integer No - -1 ~ 2147483647 Random seed for reproducibility.

### Alibaba Wan 2.1 I2V Plus 720p

- **Model ID:** `alibaba/wan-2.1/i2v-plus-720p`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/alibaba/wan-2.1/i2v-plus-720p`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/alibaba/alibaba-wan-2.1-i2v-plus-720p

**Request Parameters**

- `image`: string Yes - The image for generating the output.
- `prompt`: string Yes - The positive prompt for the generation.
- `duration`: integer No 5 5 The duration of the generated media in seconds.
- `enable_prompt_expansion`: boolean No false - If set to true, the prompt optimizer will be enabled.
- `negative_prompt`: string No - The negative prompt for the generation.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Alibaba Wan 2.1 T2V Plus 720p

- **Model ID:** `alibaba/wan-2.1/t2v-plus-720p`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/alibaba/wan-2.1/t2v-plus-720p`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/alibaba/alibaba-wan-2.1-t2v-plus-720p

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 1280*720 1280*720, 720*1280 The size of the generated media in pixels (width*height).
- `duration`: integer No 5 5 The duration of the generated media in seconds.
- `enable_prompt_expansion`: boolean No false - If set to true, the prompt optimizer will be enabled.
- `negative_prompt`: string No - The negative prompt for the generation.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Alibaba Wan 2.2 I2V Plus 1080p

- **Model ID:** `alibaba/wan-2.2/i2v-plus-1080p`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/alibaba/wan-2.2/i2v-plus-1080p`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/alibaba/alibaba-wan-2.2-i2v-plus-1080p

**Request Parameters**

- `image`: string Yes - The image for generating the output.
- `prompt`: string Yes - The positive prompt for the generation.
- `duration`: integer No 5 5 The duration of the generated media in seconds.
- `enable_prompt_expansion`: boolean No false - If set to true, the prompt optimizer will be enabled.
- `negative_prompt`: string No - The negative prompt for the generation.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Alibaba Wan 2.2 I2V Plus 480p

- **Model ID:** `alibaba/wan-2.2/i2v-plus-480p`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/alibaba/wan-2.2/i2v-plus-480p`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/alibaba/alibaba-wan-2.2-i2v-plus-480p

**Request Parameters**

- `image`: string Yes - The image for generating the output.
- `prompt`: string No - The positive prompt for the generation.
- `duration`: integer No 5 5 The duration of the generated media in seconds.
- `enable_prompt_expansion`: boolean No false - If set to true, the prompt optimizer will be enabled.
- `negative_prompt`: string No - The negative prompt for the generation.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Alibaba Wan 2.2 T2V Plus 1080p

- **Model ID:** `alibaba/wan-2.2/t2v-plus-1080p`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/alibaba/wan-2.2/t2v-plus-1080p`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/alibaba/alibaba-wan-2.2-t2v-plus-1080p

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 1920*1080 1920*1080, 1080*1920 The size of the generated media in pixels (width*height).
- `duration`: integer No 5 5 The duration of the generated media in seconds.
- `enable_prompt_expansion`: boolean No false - If set to true, the prompt optimizer will be enabled.
- `negative_prompt`: string No - The negative prompt for the generation.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Alibaba Wan 2.2 T2V Plus 480p

- **Model ID:** `alibaba/wan-2.2/t2v-plus-480p`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/alibaba/wan-2.2/t2v-plus-480p`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/alibaba/alibaba-wan-2.2-t2v-plus-480p

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 832*480 832*480, 480*832 The size of the generated media in pixels (width*height).
- `duration`: integer No 5 5 The duration of the generated media in seconds.
- `enable_prompt_expansion`: boolean No false - If set to true, the prompt optimizer will be enabled.
- `negative_prompt`: string No - The negative prompt for the generation.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Alibaba Wan 2.5 Image To Video

- **Model ID:** `alibaba/wan-2.5/image-to-video`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/alibaba/wan-2.5/image-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/alibaba/alibaba-wan-2.5-image-to-video

**Request Parameters**

- `image`: string Yes - The image for generating the output.
- `audio`: string No - - Audio URL to guide generation (optional).
- `prompt`: string Yes - The positive prompt for the generation.
- `negative_prompt`: string No - The negative prompt for the generation.
- `resolution`: string No 720p 480p, 720p, 1080p The resolution of the generated media.
- `duration`: integer No 5 3, 4, 5, 6, 7, 8, 9, 10 The duration of the generated media in seconds.
- `enable_prompt_expansion`: boolean No false - If set to true, the prompt optimizer will be enabled.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Alibaba Wan 2.5 Image To Video Fast

- **Model ID:** `alibaba/wan-2.5/image-to-video-fast`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/alibaba/wan-2.5/image-to-video-fast`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/alibaba/alibaba-wan-2.5-image-to-video-fast

**Request Parameters**

- `image`: string Yes - The image for generating the output.
- `audio`: string No - - Audio URL to guide generation (optional).
- `prompt`: string Yes - The positive prompt for the generation.
- `negative_prompt`: string No - The negative prompt for the generation.
- `resolution`: string No 720p 720p, 1080p The resolution of the generated media.
- `duration`: integer No 5 3, 4, 5, 6, 7, 8, 9, 10 The duration of the generated media in seconds.
- `enable_prompt_expansion`: boolean No false - If set to true, the prompt optimizer will be enabled.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Alibaba Wan 2.5 Text To Video

- **Model ID:** `alibaba/wan-2.5/text-to-video`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/alibaba/wan-2.5/text-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/alibaba/alibaba-wan-2.5-text-to-video

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `negative_prompt`: string No - The negative prompt for the generation.
- `audio`: string No - - Audio URL to guide generation (optional). Audio: ≥3s WAV/MP3, ≤15 MB
- `size`: string No 1280*720 832*480, 480*832, 1280*720, 720*1280, 1920*1080, 1080*1920 The size of the generated media in pixels (width*height).
- `duration`: integer No 5 5, 10 The duration of the generated media in seconds.
- `enable_prompt_expansion`: boolean No false - If set to true, the prompt optimizer will be enabled.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Alibaba Wan 2.5 Video Extend

- **Model ID:** `alibaba/wan-2.5/video-extend`
- **Operation:** `video_extend`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/alibaba/wan-2.5/video-extend`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/alibaba/alibaba-wan-2.5-video-extend

**Request Parameters**

- `video`: string Yes - The video for generating the output.
- `audio`: string No - - Audio URL to guide generation (optional).
- `prompt`: string Yes - The positive prompt for the generation.
- `negative_prompt`: string No - The negative prompt for the generation.
- `resolution`: string No 720p 480p, 720p, 1080p The resolution of the generated media.
- `duration`: integer No 5 3, 4, 5, 6, 7, 8, 9, 10 The duration of the generated media in seconds.
- `enable_prompt_expansion`: boolean No false - If set to true, the prompt optimizer will be enabled.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Alibaba Wan 2.6 Image To Video

- **Model ID:** `alibaba/wan-2.6/image-to-video`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/alibaba/wan-2.6/image-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/alibaba/alibaba-wan-2.6-image-to-video

**Request Parameters**

- `image`: string Yes - The image for generating the output.
- `audio`: string No - - Audio URL to guide generation (optional).
- `prompt`: string Yes - The positive prompt for the generation.
- `negative_prompt`: string No - The negative prompt for the generation.
- `resolution`: string No 720p 720p, 1080p The resolution of the generated media.
- `duration`: integer No 5 5, 10, 15 The duration of the generated media in seconds.
- `shot_type`: string No single single, multi The type of shots to generate.
- `enable_prompt_expansion`: boolean No false - If set to true, the prompt optimizer will be enabled.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Alibaba Wan 2.6 Image To Video Flash

- **Model ID:** `alibaba/wan-2.6/image-to-video-flash`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/alibaba/wan-2.6/image-to-video-flash`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/alibaba/alibaba-wan-2.6-image-to-video-flash

**Request Parameters**

- `image`: string Yes - The image for generating the output.
- `audio`: string No - - Audio URL to guide generation (optional).
- `prompt`: string Yes - The positive prompt for the generation.
- `negative_prompt`: string No - The negative prompt for the generation.
- `resolution`: string No 720p 720p, 1080p The resolution of the generated media.
- `duration`: integer No 5 2 ~ 15 The duration of the generated media in seconds.
- `shot_type`: string No single single, multi The type of shots to generate.
- `enable_prompt_expansion`: boolean No false - If set to true, the prompt optimizer will be enabled.
- `enable_audio`: boolean No true - If set to true, outputs video with audio. If false, outputs silent video.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Alibaba Wan 2.6 Image To Video Pro

- **Model ID:** `alibaba/wan-2.6/image-to-video-pro`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/alibaba/wan-2.6/image-to-video-pro`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/alibaba/alibaba-wan-2.6-image-to-video-pro

**Request Parameters**

- `image`: string Yes - The image for generating the output.
- `audio`: string No - - Audio URL to guide generation (optional).
- `prompt`: string Yes - The positive prompt for the generation.
- `negative_prompt`: string No - The negative prompt for the generation.
- `resolution`: string No 1080p 1080p, 2k, 4k The resolution of the generated media.
- `duration`: integer No 5 5, 10, 15 The duration of the generated media in seconds.
- `shot_type`: string No single single, multi The type of shots to generate.
- `enable_prompt_expansion`: boolean No false - If set to true, the prompt optimizer will be enabled.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Alibaba Wan 2.6 Image To Video Spicy

- **Model ID:** `alibaba/wan-2.6/image-to-video-spicy`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/alibaba/wan-2.6/image-to-video-spicy`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/alibaba/alibaba-wan-2.6-image-to-video-spicy

**Request Parameters**

- `image`: string Yes - The image for generating the output.
- `audio`: string No - - Audio URL to guide generation (optional).
- `prompt`: string No - The positive prompt for the generation.
- `negative_prompt`: string No - The negative prompt for the generation.
- `resolution`: string No 720p 720p, 1080p The resolution of the generated media.
- `duration`: integer No 5 5, 10, 15 The duration of the generated media in seconds.
- `shot_type`: string No single single, multi The type of shots to generate.
- `enable_prompt_expansion`: boolean No false - If set to true, the prompt optimizer will be enabled.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Alibaba Wan 2.6 Reference To Video

- **Model ID:** `alibaba/wan-2.6/reference-to-video`
- **Operation:** `reference_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/alibaba/wan-2.6/reference-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/alibaba/alibaba-wan-2.6-reference-to-video

**Request Parameters**

- `videos`: array Yes - 1 ~ 3 items Array of URLs to reference videos.
- `prompt`: string Yes - The positive prompt for the generation.
- `negative_prompt`: string No - The negative prompt for the generation.
- `audio`: string No - - Audio URL to guide generation (optional).
- `size`: string No 1280*720 1280*720, 720*1280, 1920*1080, 1080*1920 The size of the generated media in pixels (width*height).
- `duration`: integer No 5 5, 10 The duration of the generated media in seconds.
- `shot_type`: string No single single, multi The type of shots to generate.
- `enable_prompt_expansion`: boolean No false - If set to true, the prompt optimizer will be enabled.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Alibaba Wan 2.6 Reference To Video Flash

- **Model ID:** `alibaba/wan-2.6/reference-to-video-flash`
- **Operation:** `reference_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/alibaba/wan-2.6/reference-to-video-flash`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/alibaba/alibaba-wan-2.6-reference-to-video-flash

**Request Parameters**

- `reference_urls`: array Yes - 1 ~ 5 items Array of URLs to reference images or videos. Images: 0-5, Videos: 0-3, Total: ≤5.
- `prompt`: string Yes - The positive prompt for the generation.
- `audio`: string No - - Audio URL to guide generation (optional).
- `negative_prompt`: string No - The negative prompt for the generation.
- `size`: string No 1280*720 1280*720, 720*1280, 1920*1080, 1080*1920 The size of the generated media in pixels (width*height).
- `duration`: integer No 5 5, 10 The duration of the generated media in seconds.
- `shot_type`: string No single single, multi The type of shots to generate.
- `enable_audio`: boolean No true - Whether to generate audio for the video. Set to false to generate video without audio.
- `enable_prompt_expansion`: boolean No false - If set to true, the prompt optimizer will be enabled.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Alibaba Wan 2.6 Text To Video

- **Model ID:** `alibaba/wan-2.6/text-to-video`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/alibaba/wan-2.6/text-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/alibaba/alibaba-wan-2.6-text-to-video

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `negative_prompt`: string No - The negative prompt for the generation.
- `audio`: string No - - Audio URL to guide generation (optional).
- `size`: string No 1280*720 1280*720, 720*1280, 1920*1080, 1080*1920 The size of the generated media in pixels (width*height).
- `duration`: integer No 5 5, 10, 15 The duration of the generated media in seconds.
- `shot_type`: string No single single, multi The type of shots to generate.
- `enable_prompt_expansion`: boolean No false - If set to true, the prompt optimizer will be enabled.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Alibaba Wan 2.6 Video Extend

- **Model ID:** `alibaba/wan-2.6/video-extend`
- **Operation:** `video_extend`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/alibaba/wan-2.6/video-extend`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/alibaba/alibaba-wan-2.6-video-extend

**Request Parameters**

- `video`: string Yes - The video for generating the output.
- `audio`: string No - - Audio URL to guide generation (optional).
- `prompt`: string Yes - The positive prompt for the generation.
- `negative_prompt`: string No - The negative prompt for the generation.
- `resolution`: string No 720p 720p, 1080p The resolution of the generated media.
- `duration`: integer No 5 5, 10, 15 The duration of the generated media in seconds.
- `shot_type`: string No single single, multi The type of shots to generate.
- `enable_prompt_expansion`: boolean No false - If set to true, the prompt optimizer will be enabled.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Alibaba Wan 2.7 Image To Video

- **Model ID:** `alibaba/wan-2.7/image-to-video`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/alibaba/wan-2.7/image-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/alibaba/alibaba-wan-2.7-image-to-video

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `image`: string Yes - The first frame image for generating the video.
- `last_image`: string No - - The last frame image for generating the video (optional).
- `audio`: string No - - Audio URL to guide generation (optional).
- `negative_prompt`: string No - The negative prompt for the generation.
- `resolution`: string No 720p 720p, 1080p The resolution of the generated video.
- `duration`: integer No 5 2 ~ 15 The duration of the generated media in seconds (2-15s).
- `enable_prompt_expansion`: boolean No false - If set to true, the prompt optimizer will be enabled.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Alibaba Wan 2.7 Reference To Video

- **Model ID:** `alibaba/wan-2.7/reference-to-video`
- **Operation:** `reference_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/alibaba/wan-2.7/reference-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/alibaba/alibaba-wan-2.7-reference-to-video

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation. Reference videos and images by their index: videos are numbered first (video 1, video 2, ...), then reference_images continue the numbering (image N). E.g. with 2 videos and 1 image: 'vid
- `image`: string No - URL to a single reference image.
- `videos`: array No - - Array of reference video URLs (max 5). Combined count of reference_images and videos must be between 1 and 5. Videos are indexed starting from 1 in the prompt (e.g. 'video 1', 'video 2').
- `reference_images`: array No - - Array of reference image URLs (max 5). Combined count of reference_images and videos must be between 1 and 5. Note: reference_images are indexed after videos in the prompt. E.g. if you have 2 videos, the first reference image i
- `negative_prompt`: string No - The negative prompt for the generation.
- `resolution`: string No 720p 720p, 1080p The resolution of the generated video.
- `aspect_ratio`: string No 16:9 16:9, 9:16, 1:1, 4:3, 3:4 The aspect ratio of the generated video.
- `duration`: integer No 5 2 ~ 10 The duration of the generated media in seconds (2-10s).
- `enable_prompt_expansion`: boolean No false - If set to true, the prompt optimizer will be enabled.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Alibaba Wan 2.7 Text To Video

- **Model ID:** `alibaba/wan-2.7/text-to-video`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/alibaba/wan-2.7/text-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/alibaba/alibaba-wan-2.7-text-to-video

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `negative_prompt`: string No - The negative prompt for the generation.
- `audio`: string No - - Audio URL to guide generation (optional).
- `resolution`: string No 720p 720p, 1080p The resolution of the generated video.
- `aspect_ratio`: string No 16:9 16:9, 9:16, 1:1, 4:3, 3:4 The aspect ratio of the generated video.
- `duration`: integer No 5 2 ~ 15 The duration of the generated media in seconds (2-15s).
- `enable_prompt_expansion`: boolean No false - If set to true, the prompt optimizer will be enabled.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Alibaba Wan 2.7 Video Edit

- **Model ID:** `alibaba/wan-2.7/video-edit`
- **Operation:** `video_edit`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/alibaba/wan-2.7/video-edit`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/alibaba/alibaba-wan-2.7-video-edit

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `video`: string Yes - The source video to edit.
- `images`: array No [] - List of reference images for video editing (0-3 images, optional).
- `negative_prompt`: string No - The negative prompt for the generation.
- `resolution`: string No 720p 720p, 1080p The resolution of the generated video.
- `duration`: integer No - 0 ~ 10 Duration of the output video in seconds. 0 means use input video length (max 10s). Set 2-10 to trim from 0s to specified length.
- `audio_setting`: string No auto auto, origin Audio setting. 'auto' (default): model decides based on prompt content. 'origin': keep original audio from input video.
- `enable_prompt_expansion`: boolean No false - If set to true, the prompt optimizer will be enabled.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Alibaba Wan 2.7 Video Extend

- **Model ID:** `alibaba/wan-2.7/video-extend`
- **Operation:** `video_extend`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/alibaba/wan-2.7/video-extend`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/alibaba/alibaba-wan-2.7-video-extend

**Request Parameters**

- `video`: string Yes - The video for generating the output.
- `audio`: string No - - Audio URL to guide generation (optional).
- `prompt`: string Yes - The positive prompt for the generation.
- `negative_prompt`: string No - The negative prompt for the generation.
- `resolution`: string No 720p 720p, 1080p The resolution of the generated media.
- `duration`: integer No 5 5 ~ 15 Total duration of the final output video in seconds (5-15s).
- `enable_prompt_expansion`: boolean No false - If set to true, the prompt optimizer will be enabled.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Bria Video Eraser Mask

- **Model ID:** `bria/video-eraser/mask`
- **Operation:** `edit_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bria/video-eraser/mask`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bria/bria-video-eraser-mask

**Request Parameters**

- `video`: string Yes - The input video to erase objects from. Provide a URL to a publicly accessible video file.
- `mask_video`: string Yes - - The mask video that defines areas to erase (white regions = remove, black regions = keep). Provide a URL to a publicly accessible video file.
- `copy_audio`: boolean No true - Whether to keep the original audio in the output video (true = preserve audio, false = remove audio)

### Bria Video Eraser Prompt

- **Model ID:** `bria/video-eraser/prompt`
- **Operation:** `edit_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bria/video-eraser/prompt`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bria/bria-video-eraser-prompt

**Request Parameters**

- `prompt`: string Yes - Describe the object or element you want to remove from the video (e.g., 'women', 'car', 'person on the right')
- `video`: string Yes - The input video to erase objects from. Provide a URL to a publicly accessible video file.
- `copy_audio`: boolean No true - Whether to keep the original audio in the output video (true = preserve audio, false = remove audio)

### Bytedance Dreamina V3.0 Pro Image To Video

- **Model ID:** `bytedance/dreamina-v3.0-pro/image-to-video`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bytedance/dreamina-v3.0-pro/image-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bytedance/bytedance-dreamina-v3.0-pro-image-to-video

**Request Parameters**

- `image`: string Yes - The image to use as a reference.
- `prompt`: string Yes - The positive prompt for the generation.
- `aspect_ratio`: string No 16:9 16:9, 4:3, 1:1, 3:4, 9:16, 21:9, 9:21 The aspect ratio of the generated media.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Bytedance Dreamina V3.0 Pro Text To Video

- **Model ID:** `bytedance/dreamina-v3.0-pro/text-to-video`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bytedance/dreamina-v3.0-pro/text-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bytedance/bytedance-dreamina-v3.0-pro-text-to-video

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `aspect_ratio`: string No 16:9 16:9, 4:3, 1:1, 3:4, 9:16, 21:9, 9:21 The aspect ratio of the generated media.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Bytedance Dreamina V3.0 Image To Video 1080p

- **Model ID:** `bytedance/dreamina-v3.0/image-to-video-1080p`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bytedance/dreamina-v3.0/image-to-video-1080p`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bytedance/bytedance-dreamina-v3.0-image-to-video-1080p

**Request Parameters**

- `image`: string Yes - The image to be used for the generation.
- `prompt`: string Yes - The positive prompt for the generation.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `duration`: integer No 5 5 The duration of the generated media.

### Bytedance Dreamina V3.0 Image To Video 720p

- **Model ID:** `bytedance/dreamina-v3.0/image-to-video-720p`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bytedance/dreamina-v3.0/image-to-video-720p`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bytedance/bytedance-dreamina-v3.0-image-to-video-720p

**Request Parameters**

- `image`: string Yes - The image to be used for the generation.
- `prompt`: string Yes - The positive prompt for the generation.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `duration`: integer No 5 5 The duration of the generated media.

### Bytedance Dreamina V3.0 Text To Video 1080p

- **Model ID:** `bytedance/dreamina-v3.0/text-to-video-1080p`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bytedance/dreamina-v3.0/text-to-video-1080p`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bytedance/bytedance-dreamina-v3.0-text-to-video-1080p

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `aspect_ratio`: string No 16:9 16:9, 4:3, 1:1, 3:4, 9:16, 21:9 The aspect ratio of the generated media.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `duration`: integer No 5 5 The duration of the generated media.

### Bytedance Dreamina V3.0 Text To Video 720p

- **Model ID:** `bytedance/dreamina-v3.0/text-to-video-720p`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bytedance/dreamina-v3.0/text-to-video-720p`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bytedance/bytedance-dreamina-v3.0-text-to-video-720p

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `aspect_ratio`: string No 16:9 16:9, 4:3, 1:1, 3:4, 9:16, 21:9 The aspect ratio of the generated media.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `duration`: integer No 5 5 The duration of the generated media.

### Bytedance Seedance 2.0 Fast Image To Video

- **Model ID:** `bytedance/seedance-2.0-fast/image-to-video`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bytedance/seedance-2.0-fast/image-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bytedance/bytedance-seedance-2.0-fast-image-to-video

**Request Parameters**

- `prompt`: string Yes - Describe the scene, action, camera movement, and mood for the video.
- `image`: string Yes - Start image URL to guide the video generation.
- `last_image`: string No - - Last frame image URL for video continuation.
- `aspect_ratio`: string No - 16:9, 9:16, 4:3, 3:4, 1:1, 21:9 The aspect ratio of the generated video. If not specified, adapts to the input image.
- `resolution`: string No 720p 480p, 720p, 1080p The output video resolution.
- `duration`: integer No 5 4 ~ 15 The duration of the generated video in seconds (4-15s).
- `enable_web_search`: boolean No false - Enable web search for real-time information.
- `generate_audio`: boolean No true - Whether to generate native audio synchronized with the output video. Defaults to true.

### Bytedance Seedance 2.0 Fast Image To Video Turbo

- **Model ID:** `bytedance/seedance-2.0-fast/image-to-video-turbo`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bytedance/seedance-2.0-fast/image-to-video-turbo`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bytedance/bytedance-seedance-2.0-fast-image-to-video-turbo

**Request Parameters**

- `prompt`: string Yes - Describe the scene, action, camera movement, and mood for the video.
- `image`: string Yes - Start image URL to guide the video generation.
- `last_image`: string No - - Last frame image URL for video continuation.
- `aspect_ratio`: string No - 16:9, 9:16, 4:3, 3:4, 1:1, 21:9 The aspect ratio of the generated video. If not specified, adapts to the input image.
- `resolution`: string No 720p 720p, 1080p The output video resolution.
- `duration`: integer No 5 4 ~ 15 The duration of the generated video in seconds (4-15s).
- `enable_web_search`: boolean No false - Enable web search for real-time information.
- `generate_audio`: boolean No true - Whether to generate native audio synchronized with the output video. Defaults to true.

### Bytedance Seedance 2.0 Fast Text To Video

- **Model ID:** `bytedance/seedance-2.0-fast/text-to-video`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bytedance/seedance-2.0-fast/text-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bytedance/bytedance-seedance-2.0-fast-text-to-video

**Request Parameters**

- `prompt`: string Yes - Describe the scene, action, camera movement, and mood for the video.
- `reference_images`: array No - - Reference image URLs to guide visual style, characters, or scene composition.
- `reference_videos`: array No - - Reference video URLs (total length must not exceed 15 seconds).
- `reference_audios`: array No - - Reference audio URLs (total length must not exceed 15 seconds).
- `aspect_ratio`: string No 16:9 16:9, 9:16, 4:3, 3:4, 1:1, 21:9 The aspect ratio of the generated video.
- `resolution`: string No 720p 480p, 720p, 1080p The output video resolution.
- `duration`: integer No 5 4 ~ 15 The duration of the generated video in seconds (4-15s).
- `enable_web_search`: boolean No false - Enable web search for real-time information.
- `generate_audio`: boolean No true - Whether to generate native audio synchronized with the output video. Defaults to true.

### Bytedance Seedance 2.0 Fast Text To Video Turbo

- **Model ID:** `bytedance/seedance-2.0-fast/text-to-video-turbo`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bytedance/seedance-2.0-fast/text-to-video-turbo`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bytedance/bytedance-seedance-2.0-fast-text-to-video-turbo

**Request Parameters**

- `prompt`: string Yes - Describe the scene, action, camera movement, and mood for the video.
- `reference_images`: array No - - Reference image URLs to guide visual style, characters, or scene composition.
- `reference_videos`: array No - - Reference video URLs (total length must not exceed 15 seconds).
- `reference_audios`: array No - - Reference audio URLs (total length must not exceed 15 seconds).
- `aspect_ratio`: string No 16:9 16:9, 9:16, 4:3, 3:4, 1:1, 21:9 The aspect ratio of the generated video.
- `resolution`: string No 720p 720p, 1080p The output video resolution.
- `duration`: integer No 5 4 ~ 15 The duration of the generated video in seconds (4-15s).
- `enable_web_search`: boolean No false - Enable web search for real-time information.
- `generate_audio`: boolean No true - Whether to generate native audio synchronized with the output video. Defaults to true.

### Bytedance Seedance 2.0 Fast Video Edit

- **Model ID:** `bytedance/seedance-2.0-fast/video-edit`
- **Operation:** `video_edit`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bytedance/seedance-2.0-fast/video-edit`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bytedance/bytedance-seedance-2.0-fast-video-edit

**Request Parameters**

- `prompt`: string Yes - Describe the edit you want applied to the input video. The prefix "Edit the input video." is added automatically.
- `video`: string Yes - URL of the input video to edit. Videos longer than 15s are trimmed to 15s.
- `reference_images`: array No - - Optional reference image URLs to guide the edit (subject identity, style, etc.).
- `reference_audios`: array No - - Optional reference audio URLs to guide audio generation.
- `aspect_ratio`: string No - 16:9, 9:16, 4:3, 3:4, 1:1, 21:9 Aspect ratio of the output video. Adapts to the input if not specified.
- `resolution`: string No 720p 480p, 720p, 1080p Output video resolution.
- `duration`: integer No - 4 ~ 15 Output video length in seconds (4-15). Auto-detected from the input video if not specified.
- `enable_web_search`: boolean No false - Enable web search for real-time information.
- `generate_audio`: boolean No true - Whether to generate native audio for the edited output. Defaults to true. When set to false, the input video's audio track is preserved on the output instead.

### Bytedance Seedance 2.0 Fast Video Edit Turbo

- **Model ID:** `bytedance/seedance-2.0-fast/video-edit-turbo`
- **Operation:** `video_edit`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bytedance/seedance-2.0-fast/video-edit-turbo`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bytedance/bytedance-seedance-2.0-fast-video-edit-turbo

**Request Parameters**

- `prompt`: string Yes - Describe the edit you want applied to the input video. The prefix "Edit the input video." is added automatically.
- `video`: string Yes - URL of the input video to edit. Videos longer than 15s are trimmed to 15s.
- `reference_images`: array No - - Optional reference image URLs to guide the edit (subject identity, style, etc.).
- `reference_audios`: array No - - Optional reference audio URLs to guide audio generation.
- `aspect_ratio`: string No - 16:9, 9:16, 4:3, 3:4, 1:1, 21:9 Aspect ratio of the output video. Adapts to the input if not specified.
- `resolution`: string No 720p 720p, 1080p Turbo output resolution.
- `duration`: integer No - 4 ~ 15 Output video length in seconds (4-15). Auto-detected from the input video if not specified.
- `enable_web_search`: boolean No false - Enable web search for real-time information.
- `generate_audio`: boolean No true - Whether to generate native audio for the edited output. Defaults to true. When set to false, the input video's audio track is preserved on the output instead.

### Bytedance Seedance 2.0 Fast Video Extend

- **Model ID:** `bytedance/seedance-2.0-fast/video-extend`
- **Operation:** `video_extend`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bytedance/seedance-2.0-fast/video-extend`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bytedance/bytedance-seedance-2.0-fast-video-extend

**Request Parameters**

- `prompt`: string Yes - Describe the cinematic continuation — action, camera movement, lighting, mood.
- `video`: string Yes - URL of the input video to extend. Generation continues from the last frame.
- `last_image`: string No - - Optional target last-frame URL. If provided, the new segment interpolates from the input video's last frame to this image.
- `resolution`: string No 720p 480p, 720p, 1080p Output resolution of the new segment.
- `duration`: integer No 5 4 ~ 15 Length in seconds of the new segment to append (4-15).
- `enable_web_search`: boolean No false - Enable web search for real-time information.
- `generate_audio`: boolean No true - Whether to generate native audio synchronized with the output video. Defaults to true.

### Bytedance Seedance 2.0 Image To Video

- **Model ID:** `bytedance/seedance-2.0/image-to-video`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bytedance/seedance-2.0/image-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bytedance/bytedance-seedance-2.0-image-to-video

**Request Parameters**

- `prompt`: string Yes - Describe the scene, action, camera movement, and mood for the video.
- `image`: string Yes - Start image URL to guide the video generation.
- `last_image`: string No - - Last frame image URL for video continuation.
- `aspect_ratio`: string No - 16:9, 9:16, 4:3, 3:4, 1:1, 21:9 The aspect ratio of the generated video. If not specified, adapts to the input image.
- `resolution`: string No 720p 480p, 720p, 1080p The output video resolution.
- `duration`: integer No 5 4 ~ 15 The duration of the generated video in seconds (4-15s).
- `enable_web_search`: boolean No false - Enable web search for real-time information.
- `generate_audio`: boolean No true - Whether to generate native audio synchronized with the output video. Defaults to true.

### Bytedance Seedance 2.0 Image To Video Turbo

- **Model ID:** `bytedance/seedance-2.0/image-to-video-turbo`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bytedance/seedance-2.0/image-to-video-turbo`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bytedance/bytedance-seedance-2.0-image-to-video-turbo

**Request Parameters**

- `prompt`: string Yes - Describe the scene, action, camera movement, and mood for the video.
- `image`: string Yes - Start image URL to guide the video generation.
- `last_image`: string No - - Last frame image URL for video continuation.
- `aspect_ratio`: string No - 16:9, 9:16, 4:3, 3:4, 1:1, 21:9 The aspect ratio of the generated video. If not specified, adapts to the input image.
- `resolution`: string No 720p 720p, 1080p The output video resolution.
- `duration`: integer No 5 4 ~ 15 The duration of the generated video in seconds (4-15s).
- `enable_web_search`: boolean No false - Enable web search for real-time information.
- `generate_audio`: boolean No true - Whether to generate native audio synchronized with the output video. Defaults to true.

### Bytedance Seedance 2.0 Text To Video

- **Model ID:** `bytedance/seedance-2.0/text-to-video`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bytedance/seedance-2.0/text-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bytedance/bytedance-seedance-2.0-text-to-video

**Request Parameters**

- `prompt`: string Yes - Describe the scene, action, camera movement, and mood for the video.
- `reference_images`: array No - - Reference image URLs to guide visual style, characters, or scene composition.
- `reference_videos`: array No - - Reference video URLs (total length must not exceed 15 seconds).
- `reference_audios`: array No - - Reference audio URLs (total length must not exceed 15 seconds).
- `aspect_ratio`: string No 16:9 16:9, 9:16, 4:3, 3:4, 1:1, 21:9 The aspect ratio of the generated video.
- `resolution`: string No 720p 480p, 720p, 1080p The output video resolution.
- `duration`: integer No 5 4 ~ 15 The duration of the generated video in seconds (4-15s).
- `enable_web_search`: boolean No false - Enable web search for real-time information.
- `generate_audio`: boolean No true - Whether to generate native audio synchronized with the output video. Defaults to true.

### Bytedance Seedance 2.0 Text To Video Turbo

- **Model ID:** `bytedance/seedance-2.0/text-to-video-turbo`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bytedance/seedance-2.0/text-to-video-turbo`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bytedance/bytedance-seedance-2.0-text-to-video-turbo

**Request Parameters**

- `prompt`: string Yes - Describe the scene, action, camera movement, and mood for the video.
- `reference_images`: array No - - Reference image URLs to guide visual style, characters, or scene composition.
- `reference_videos`: array No - - Reference video URLs (total length must not exceed 15 seconds).
- `reference_audios`: array No - - Reference audio URLs (total length must not exceed 15 seconds).
- `aspect_ratio`: string No 16:9 16:9, 9:16, 4:3, 3:4, 1:1, 21:9 The aspect ratio of the generated video.
- `resolution`: string No 720p 720p, 1080p The output video resolution.
- `duration`: integer No 5 4 ~ 15 The duration of the generated video in seconds (4-15s).
- `enable_web_search`: boolean No false - Enable web search for real-time information.
- `generate_audio`: boolean No true - Whether to generate native audio synchronized with the output video. Defaults to true.

### Bytedance Seedance 2.0 Video Edit

- **Model ID:** `bytedance/seedance-2.0/video-edit`
- **Operation:** `video_edit`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bytedance/seedance-2.0/video-edit`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bytedance/bytedance-seedance-2.0-video-edit

**Request Parameters**

- `prompt`: string Yes - Describe the edit you want applied to the input video. The prefix "Edit the input video." is added automatically.
- `video`: string Yes - URL of the input video to edit. Videos longer than 15s are trimmed to 15s.
- `reference_images`: array No - - Optional reference image URLs to guide the edit (subject identity, style, etc.).
- `reference_audios`: array No - - Optional reference audio URLs to guide audio generation.
- `aspect_ratio`: string No - 16:9, 9:16, 4:3, 3:4, 1:1, 21:9 Aspect ratio of the output video. Adapts to the input if not specified.
- `resolution`: string No 720p 480p, 720p, 1080p Output video resolution.
- `duration`: integer No - 4 ~ 15 Output video length in seconds (4-15). Auto-detected from the input video if not specified.
- `enable_web_search`: boolean No false - Enable web search for real-time information.
- `generate_audio`: boolean No true - Whether to generate native audio for the edited output. Defaults to true. When set to false, the input video's audio track is preserved on the output instead.

### Bytedance Seedance 2.0 Video Edit Turbo

- **Model ID:** `bytedance/seedance-2.0/video-edit-turbo`
- **Operation:** `video_edit`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bytedance/seedance-2.0/video-edit-turbo`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bytedance/bytedance-seedance-2.0-video-edit-turbo

**Request Parameters**

- `prompt`: string Yes - Describe the edit you want applied to the input video. The prefix "Edit the input video." is added automatically.
- `video`: string Yes - URL of the input video to edit. Videos longer than 15s are trimmed to 15s.
- `reference_images`: array No - - Optional reference image URLs to guide the edit (subject identity, style, etc.).
- `reference_audios`: array No - - Optional reference audio URLs to guide audio generation.
- `aspect_ratio`: string No - 16:9, 9:16, 4:3, 3:4, 1:1, 21:9 Aspect ratio of the output video. Adapts to the input if not specified.
- `resolution`: string No 720p 720p, 1080p Turbo output resolution.
- `duration`: integer No - 4 ~ 15 Output video length in seconds (4-15). Auto-detected from the input video if not specified.
- `enable_web_search`: boolean No false - Enable web search for real-time information.
- `generate_audio`: boolean No true - Whether to generate native audio for the edited output. Defaults to true. When set to false, the input video's audio track is preserved on the output instead.

### Bytedance Seedance 2.0 Video Extend

- **Model ID:** `bytedance/seedance-2.0/video-extend`
- **Operation:** `video_extend`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bytedance/seedance-2.0/video-extend`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bytedance/bytedance-seedance-2.0-video-extend

**Request Parameters**

- `prompt`: string Yes - Describe the cinematic continuation — action, camera movement, lighting, mood.
- `video`: string Yes - URL of the input video to extend. Generation continues from the last frame.
- `last_image`: string No - - Optional target last-frame URL. If provided, the new segment interpolates from the input video's last frame to this image.
- `resolution`: string No 720p 480p, 720p, 1080p Output resolution of the new segment.
- `duration`: integer No 5 4 ~ 15 Length in seconds of the new segment to append (4-15).
- `enable_web_search`: boolean No false - Enable web search for real-time information.
- `generate_audio`: boolean No true - Whether to generate native audio synchronized with the output video. Defaults to true.

### Bytedance Seedance V1 Lite I2V 1080p

- **Model ID:** `bytedance/seedance-v1-lite-i2v-1080p`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bytedance/seedance-v1-lite-i2v-1080p`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bytedance/bytedance-seedance-v1-lite-i2v-1080p

**Request Parameters**

- `image`: string Yes - Input image supports both URL and Base64 format; The image file size cannot exceed 30MB, and the image resolution should not be less than 300*300px.
- `prompt`: string No - The positive prompt for the generation. max length 2000
- `last_image`: string No - - URL of the ending image.
- `duration`: integer No 5 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12 The duration of the generated media in seconds.
- `aspect_ratio`: string No - 21:9, 16:9, 4:3, 1:1, 3:4, 9:16 The aspect ratio of the generated media.
- `camera_fixed`: boolean No false - Whether to fix the camera position.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Bytedance Seedance V1 Lite I2V 480p

- **Model ID:** `bytedance/seedance-v1-lite-i2v-480p`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bytedance/seedance-v1-lite-i2v-480p`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bytedance/bytedance-seedance-v1-lite-i2v-480p

**Request Parameters**

- `image`: string Yes - Input image supports both URL and Base64 format; Supported image formats include .jpg/.jpeg/.png; The image file size cannot exceed 10MB, and the image resolution should not be less than 300*300px.
- `prompt`: string No - The positive prompt for the generation.max length 2000
- `last_image`: string No - - End image supports both URL and Base64 format.
- `duration`: integer No 5 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12 The duration of the generated media in seconds.
- `aspect_ratio`: string No - 21:9, 16:9, 4:3, 1:1, 3:4, 9:16 The aspect ratio of the generated media.
- `camera_fixed`: boolean No false - Whether to fix the camera position.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Bytedance Seedance V1 Lite I2V 720p

- **Model ID:** `bytedance/seedance-v1-lite-i2v-720p`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bytedance/seedance-v1-lite-i2v-720p`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bytedance/bytedance-seedance-v1-lite-i2v-720p

**Request Parameters**

- `image`: string Yes - Input image supports both URL and Base64 format; Supported image formats include .jpg/.jpeg/.png; The image file size cannot exceed 10MB, and the image resolution should not be less than 300*300px.
- `prompt`: string No - The positive prompt for the generation.max length 2000
- `last_image`: string No - - End image, supports both URL and Base64 format.
- `duration`: integer No 5 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12 The duration of the generated media in seconds.
- `aspect_ratio`: string No - 21:9, 16:9, 4:3, 1:1, 3:4, 9:16 The aspect ratio of the generated media.
- `camera_fixed`: boolean No false - Whether to fix the camera position.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Bytedance Seedance V1 Lite T2V 1080p

- **Model ID:** `bytedance/seedance-v1-lite-t2v-1080p`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bytedance/seedance-v1-lite-t2v-1080p`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bytedance/bytedance-seedance-v1-lite-t2v-1080p

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.max length 2000
- `aspect_ratio`: string No 16:9 21:9, 16:9, 4:3, 1:1, 3:4, 9:16 The aspect ratio of the generated media.
- `duration`: integer No 5 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12 The duration of the generated media in seconds.
- `camera_fixed`: boolean No false - Whether to fix the camera position.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Bytedance Seedance V1 Lite T2V 480p

- **Model ID:** `bytedance/seedance-v1-lite-t2v-480p`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bytedance/seedance-v1-lite-t2v-480p`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bytedance/bytedance-seedance-v1-lite-t2v-480p

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation. max length 2000
- `aspect_ratio`: string No 16:9 21:9, 16:9, 4:3, 1:1, 3:4, 9:16 The aspect ratio of the generated media.
- `duration`: integer Yes 5 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12 The duration of the generated media in seconds.
- `camera_fixed`: boolean No false - Whether to fix the camera position.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Bytedance Seedance V1 Lite T2V 720p

- **Model ID:** `bytedance/seedance-v1-lite-t2v-720p`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bytedance/seedance-v1-lite-t2v-720p`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bytedance/bytedance-seedance-v1-lite-t2v-720p

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.max length 2000
- `aspect_ratio`: string No 16:9 21:9, 16:9, 4:3, 1:1, 3:4, 9:16 The aspect ratio of the generated media.
- `duration`: integer Yes 5 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12 The duration of the generated media in seconds.
- `camera_fixed`: boolean No false - Whether to fix the camera position.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Bytedance Seedance V1 Lite Reference To Video

- **Model ID:** `bytedance/seedance-v1-lite/reference-to-video`
- **Operation:** `reference_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bytedance/seedance-v1-lite/reference-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bytedance/bytedance-seedance-v1-lite-reference-to-video

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `reference_images`: array Yes - 1 ~ 4 items A list of images to use as style references. At least 1 image is required. max 4 images.
- `duration`: integer No 5 2 ~ 12 The duration of the generated media in seconds.
- `camera_fixed`: boolean No false - Whether to fix the camera position.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Bytedance Seedance V1 Pro Fast Image To Video

- **Model ID:** `bytedance/seedance-v1-pro-fast/image-to-video`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bytedance/seedance-v1-pro-fast/image-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bytedance/bytedance-seedance-v1-pro-fast-image-to-video

**Request Parameters**

- `image`: string Yes - Input image for video generation; Supported image formats include .jpg/.jpeg/.png; The image file size cannot exceed 10MB, and the image resolution should not be less than 300*300px.
- `prompt`: string Yes - The positive prompt for the generation.max length 2000
- `resolution`: string No 480p 480p, 720p, 1080p The resolution of the generated media.
- `duration`: integer No 5 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12 The duration of the generated media in seconds.
- `aspect_ratio`: string No - 21:9, 16:9, 4:3, 1:1, 3:4, 9:16 The aspect ratio of the generated media.
- `camera_fixed`: boolean No false - Whether to fix the camera position.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Bytedance Seedance V1 Pro Fast Text To Video

- **Model ID:** `bytedance/seedance-v1-pro-fast/text-to-video`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bytedance/seedance-v1-pro-fast/text-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bytedance/bytedance-seedance-v1-pro-fast-text-to-video

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `resolution`: string No 480p 480p, 720p, 1080p The resolution of the generated media.
- `duration`: integer No 5 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12 The duration of the generated media in seconds.
- `aspect_ratio`: string No 16:9 21:9, 16:9, 4:3, 1:1, 3:4, 9:16 The aspect ratio of the generated media.
- `camera_fixed`: boolean No false - Whether to fix the camera position.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Bytedance Seedance V1 Pro I2V 1080p

- **Model ID:** `bytedance/seedance-v1-pro-i2v-1080p`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bytedance/seedance-v1-pro-i2v-1080p`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bytedance/bytedance-seedance-v1-pro-i2v-1080p

**Request Parameters**

- `image`: string Yes - Input image for video generation; Supported image formats include .jpg/.jpeg/.png; The image file size cannot exceed 10MB, and the image resolution should not be less than 300*300px.
- `prompt`: string No - The positive prompt for the generation.max length 2000
- `last_image`: string No - - End image, supports both URL and Base64 format.
- `duration`: integer No 5 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12 The duration of the generated media in seconds.
- `aspect_ratio`: string No - 21:9, 16:9, 4:3, 1:1, 3:4, 9:16 The aspect ratio of the generated media.
- `camera_fixed`: boolean No false - Whether to fix the camera position.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Bytedance Seedance V1 Pro I2V 480p

- **Model ID:** `bytedance/seedance-v1-pro-i2v-480p`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bytedance/seedance-v1-pro-i2v-480p`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bytedance/bytedance-seedance-v1-pro-i2v-480p

**Request Parameters**

- `image`: string Yes - Input image for video generation; Supported image formats include .jpg/.jpeg/.png; The image file size cannot exceed 10MB, and the image resolution should not be less than 300*300px.
- `prompt`: string No - The positive prompt for the generation.max length 2000
- `last_image`: string No - - End image, supports both URL and Base64 format.
- `duration`: integer Yes 5 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12 The duration of the generated media in seconds.
- `aspect_ratio`: string No - 21:9, 16:9, 4:3, 1:1, 3:4, 9:16 The aspect ratio of the generated media.
- `camera_fixed`: boolean No false - Whether to fix the camera position.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Bytedance Seedance V1 Pro I2V 720p

- **Model ID:** `bytedance/seedance-v1-pro-i2v-720p`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bytedance/seedance-v1-pro-i2v-720p`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bytedance/bytedance-seedance-v1-pro-i2v-720p

**Request Parameters**

- `image`: string Yes - Input image for video generation; Supported image formats include .jpg/.jpeg/.png; The image file size cannot exceed 10MB, and the image resolution should not be less than 300*300px.
- `prompt`: string No - The positive prompt for the generation.max length 2000
- `last_image`: string No - - End image, supports both URL and Base64 format.
- `duration`: integer Yes 5 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12 The duration of the generated media in seconds.
- `aspect_ratio`: string No - 21:9, 16:9, 4:3, 1:1, 3:4, 9:16 The aspect ratio of the generated media.
- `camera_fixed`: boolean No false - Whether to fix the camera position.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Bytedance Seedance V1 Pro T2V 1080p

- **Model ID:** `bytedance/seedance-v1-pro-t2v-1080p`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bytedance/seedance-v1-pro-t2v-1080p`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bytedance/bytedance-seedance-v1-pro-t2v-1080p

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `aspect_ratio`: string No 16:9 21:9, 16:9, 4:3, 1:1, 3:4, 9:16 The aspect ratio of the generated media.
- `duration`: integer No 5 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12 The duration of the generated media in seconds.
- `camera_fixed`: boolean No false - Whether to fix the camera position.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Bytedance Seedance V1 Pro T2V 480p

- **Model ID:** `bytedance/seedance-v1-pro-t2v-480p`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bytedance/seedance-v1-pro-t2v-480p`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bytedance/bytedance-seedance-v1-pro-t2v-480p

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `aspect_ratio`: string No 16:9 21:9, 16:9, 4:3, 1:1, 3:4, 9:16 The aspect ratio of the generated media.
- `duration`: integer No 5 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12 The duration of the generated media in seconds.
- `camera_fixed`: boolean No false - Whether to fix the camera position.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Bytedance Seedance V1 Pro T2V 720p

- **Model ID:** `bytedance/seedance-v1-pro-t2v-720p`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bytedance/seedance-v1-pro-t2v-720p`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bytedance/bytedance-seedance-v1-pro-t2v-720p

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `aspect_ratio`: string No 16:9 21:9, 16:9, 4:3, 1:1, 3:4, 9:16 The aspect ratio of the generated media.
- `duration`: integer No 5 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12 The duration of the generated media in seconds.
- `camera_fixed`: boolean No false - Whether to fix the camera position.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Bytedance Seedance V1.5 Pro Image To Video

- **Model ID:** `bytedance/seedance-v1.5-pro/image-to-video`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bytedance/seedance-v1.5-pro/image-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bytedance/bytedance-seedance-v1.5-pro-image-to-video

**Request Parameters**

- `image`: string Yes - The positive prompt for the generation.
- `prompt`: string Yes - The positive prompt for the generation.
- `last_image`: string No - - The positive prompt for the generation.
- `aspect_ratio`: string No - 21:9, 16:9, 4:3, 1:1, 3:4, 9:16 The aspect ratio of the generated media.
- `duration`: integer No 5 4 ~ 12 The duration of the generated media in seconds.
- `resolution`: string No 720p 480p, 720p, 1080p Video resolution.
- `generate_audio`: boolean No true - Whether to generate audio.
- `camera_fixed`: boolean No false - Whether to fix the camera position.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Bytedance Seedance V1.5 Pro Image To Video Fast

- **Model ID:** `bytedance/seedance-v1.5-pro/image-to-video-fast`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bytedance/seedance-v1.5-pro/image-to-video-fast`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bytedance/bytedance-seedance-v1.5-pro-image-to-video-fast

**Request Parameters**

- `image`: string Yes - The positive prompt for the generation.
- `prompt`: string Yes - The positive prompt for the generation.
- `last_image`: string No - - The positive prompt for the generation.
- `aspect_ratio`: string No - 21:9, 16:9, 4:3, 1:1, 3:4, 9:16 The aspect ratio of the generated media.
- `duration`: integer No 5 4 ~ 12 The duration of the generated media in seconds.
- `resolution`: string No 720p 720p, 1080p Video resolution.
- `generate_audio`: boolean No true - Whether to generate audio.
- `camera_fixed`: boolean No false - Whether to fix the camera position.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Bytedance Seedance V1.5 Pro Image To Video Spicy

- **Model ID:** `bytedance/seedance-v1.5-pro/image-to-video-spicy`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bytedance/seedance-v1.5-pro/image-to-video-spicy`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bytedance/bytedance-seedance-v1.5-pro-image-to-video-spicy

**Request Parameters**

- `image`: string Yes - The positive prompt for the generation.
- `prompt`: string No - The positive prompt for the generation.
- `last_image`: string No - - The positive prompt for the generation.
- `aspect_ratio`: string No - 21:9, 16:9, 4:3, 1:1, 3:4, 9:16 The aspect ratio of the generated media.
- `duration`: integer No 5 4 ~ 12 The duration of the generated media in seconds.
- `resolution`: string No 720p 480p, 720p, 1080p Video resolution.
- `generate_audio`: boolean No true - Whether to generate audio.
- `camera_fixed`: boolean No false - Whether to fix the camera position.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Bytedance Seedance V1.5 Pro Text To Video

- **Model ID:** `bytedance/seedance-v1.5-pro/text-to-video`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bytedance/seedance-v1.5-pro/text-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bytedance/bytedance-seedance-v1.5-pro-text-to-video

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `aspect_ratio`: string No 16:9 21:9, 16:9, 4:3, 1:1, 3:4, 9:16 The aspect ratio of the generated media.
- `duration`: integer No 5 4 ~ 12 The duration of the generated media in seconds.
- `resolution`: string No 720p 480p, 720p, 1080p Video resolution.
- `generate_audio`: boolean No true - Whether to generate audio.
- `camera_fixed`: boolean No false - Whether to fix the camera position.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Bytedance Seedance V1.5 Pro Text To Video Fast

- **Model ID:** `bytedance/seedance-v1.5-pro/text-to-video-fast`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bytedance/seedance-v1.5-pro/text-to-video-fast`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bytedance/bytedance-seedance-v1.5-pro-text-to-video-fast

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `aspect_ratio`: string No 16:9 21:9, 16:9, 4:3, 1:1, 3:4, 9:16 The aspect ratio of the generated media.
- `duration`: integer No 5 4 ~ 12 The duration of the generated media in seconds.
- `resolution`: string No 720p 720p, 1080p Video resolution.
- `generate_audio`: boolean No true - Whether to generate audio.
- `camera_fixed`: boolean No false - Whether to fix the camera position.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Bytedance Seedance V1.5 Pro Video Extend

- **Model ID:** `bytedance/seedance-v1.5-pro/video-extend`
- **Operation:** `video_extend`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bytedance/seedance-v1.5-pro/video-extend`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bytedance/bytedance-seedance-v1.5-pro-video-extend

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `video`: string Yes - The video URL to extend.
- `duration`: integer No 5 4 ~ 12 The duration of the generated media in seconds.
- `resolution`: string No 720p 480p, 720p, 1080p Video resolution.
- `generate_audio`: boolean No true - Whether to generate audio.
- `camera_fixed`: boolean No false - Whether to fix the camera position.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Bytedance Seedance V1.5 Pro Video Extend Fast

- **Model ID:** `bytedance/seedance-v1.5-pro/video-extend-fast`
- **Operation:** `video_extend`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/bytedance/seedance-v1.5-pro/video-extend-fast`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/bytedance/bytedance-seedance-v1.5-pro-video-extend-fast

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `video`: string Yes - The video URL to extend.
- `duration`: integer No 5 4 ~ 12 The duration of the generated media in seconds.
- `resolution`: string No 720p 720p, 1080p Video resolution.
- `generate_audio`: boolean No true - Whether to generate audio.
- `camera_fixed`: boolean No false - Whether to fix the camera position.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Character AI Ovi Image To Video

- **Model ID:** `character-ai/ovi/image-to-video`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/character-ai/ovi/image-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/character-ai/character-ai-ovi-image-to-video

**Request Parameters**

- `image`: string Yes - The image for generating the output.
- `prompt`: string Yes - The positive prompt for the generation.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Character AI Ovi Text To Video

- **Model ID:** `character-ai/ovi/text-to-video`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/character-ai/ovi/text-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/character-ai/character-ai-ovi-text-to-video

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 960*540 960*540, 540*960 The size of the generated media in pixels (width*height).
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Decart Lucy Image To Video

- **Model ID:** `decart/lucy-image-to-video`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/decart/lucy-image-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/decart/decart-lucy-image-to-video

**Request Parameters**

- `image`: string Yes - The image to use as the first frame of the video.
- `prompt`: string Yes - The positive prompt for the generation.

### Google Veo2 Image To Video

- **Model ID:** `google/veo2/image-to-video`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/google/veo2/image-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/google/google-veo2-image-to-video

**Request Parameters**

- `image`: string Yes - The image to use for the generation.
- `prompt`: string Yes - The positive prompt for the generation.
- `aspect_ratio`: string No 16:9 16:9, 9:16 The aspect ratio of the generated media.
- `duration`: integer No 5 5, 6, 7, 8 The duration of the generated media in seconds.
- `resolution`: string No 720p 720p Video resolution.
- `enable_prompt_expansion`: boolean No true - If set to true, the prompt optimizer will be enabled.
- `negative_prompt`: string No - The negative prompt for the generation.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation.

### Google Veo3 Fast Image To Video

- **Model ID:** `google/veo3-fast/image-to-video`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/google/veo3-fast/image-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/google/google-veo3-fast-image-to-video

**Request Parameters**

- `image`: string Yes - The image to use for the generation.
- `prompt`: string Yes - Text prompt for generation; Positive text prompt.
- `aspect_ratio`: string No 16:9 16:9, 9:16 Aspect ratio of the video.
- `duration`: integer No 8 8, 4, 6 The duration of the generated media in seconds.
- `resolution`: string No 720p 720p, 1080p Video resolution.
- `generate_audio`: boolean No true - Whether to generate audio.
- `negative_prompt`: string No - Negative prompt for the generation.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation.

### Google Veo3.1 Fast Image To Video

- **Model ID:** `google/veo3.1-fast/image-to-video`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/google/veo3.1-fast/image-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/google/google-veo3.1-fast-image-to-video

**Request Parameters**

- `image`: string Yes - The image to use for the generation.
- `prompt`: string Yes - The positive prompt for the generation.
- `last_image`: string No - - The end image for generating the output.
- `aspect_ratio`: string No 16:9 16:9, 9:16 The aspect ratio of the generated media.
- `duration`: integer No 8 8, 4, 6 The duration of the generated media in seconds.
- `resolution`: string No 1080p 720p, 1080p, 4k Video resolution.
- `generate_audio`: boolean No true - Whether to generate audio.
- `negative_prompt`: string No - The negative prompt for the generation.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation.

### Google Veo3.1 Fast Text To Video

- **Model ID:** `google/veo3.1-fast/text-to-video`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/google/veo3.1-fast/text-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/google/google-veo3.1-fast-text-to-video

**Request Parameters**

- `prompt`: string Yes - Text prompt for generation; Positive text prompt.
- `aspect_ratio`: string No 16:9 16:9, 9:16 Aspect ratio of the video.
- `duration`: integer No 8 8, 4, 6 The duration of the generated media in seconds.
- `resolution`: string No 1080p 720p, 1080p, 4k Video resolution.
- `generate_audio`: boolean No true - Whether to generate audio.
- `negative_prompt`: string No - Negative prompt for the generation.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation.

### Google Veo3.1 Fast Video Extend

- **Model ID:** `google/veo3.1-fast/video-extend`
- **Operation:** `video_extend`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/google/veo3.1-fast/video-extend`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/google/google-veo3.1-fast-video-extend

**Request Parameters**

- `video`: string Yes - The video to use for the generation.
- `prompt`: string No - The positive prompt for the generation.
- `resolution`: string No 1080p 720p, 1080p Video resolution.
- `negative_prompt`: string No - The negative prompt for the generation.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation.

### Google Veo3.1 Lite Image To Video

- **Model ID:** `google/veo3.1-lite/image-to-video`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/google/veo3.1-lite/image-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/google/google-veo3.1-lite-image-to-video

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `image`: string Yes - The image to use for the generation.
- `aspect_ratio`: string No 16:9 16:9, 9:16 The aspect ratio of the generated media.
- `duration`: integer No 8 8, 6, 4 The duration of the generated media in seconds.
- `resolution`: string No 720p 720p, 1080p Video resolution.
- `negative_prompt`: string No - The negative prompt for the generation.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation.

### Google Veo3.1 Lite Start End To Video

- **Model ID:** `google/veo3.1-lite/start-end-to-video`
- **Operation:** `frame_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/google/veo3.1-lite/start-end-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/google/google-veo3.1-lite-start-end-to-video

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `image`: string Yes - The image to use for the generation.
- `last_image`: string Yes - - The last image to use for the generation.
- `aspect_ratio`: string No 16:9 16:9, 9:16 The aspect ratio of the generated media.
- `resolution`: string No 720p 720p, 1080p Video resolution.
- `negative_prompt`: string No - The negative prompt for the generation.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation.

### Google Veo3.1 Lite Text To Video

- **Model ID:** `google/veo3.1-lite/text-to-video`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/google/veo3.1-lite/text-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/google/google-veo3.1-lite-text-to-video

**Request Parameters**

- `prompt`: string Yes - Text prompt for generation; Positive text prompt.
- `aspect_ratio`: string No 16:9 16:9, 9:16 Aspect ratio of the video.
- `duration`: integer No 6 8, 6, 4 The duration of the generated media in seconds.
- `resolution`: string No 720p 720p, 1080p Video resolution.
- `negative_prompt`: string No - Negative prompt for the generation.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation.

### Google Veo3.1 Image To Video

- **Model ID:** `google/veo3.1/image-to-video`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/google/veo3.1/image-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/google/google-veo3.1-image-to-video

**Request Parameters**

- `image`: string Yes - The image to use for the generation.
- `prompt`: string Yes - The positive prompt for the generation.
- `last_image`: string No - - The end image for generating the output.
- `aspect_ratio`: string No 16:9 16:9, 9:16 The aspect ratio of the generated media.
- `duration`: integer No 8 8, 4, 6 The duration of the generated media in seconds.
- `resolution`: string No 1080p 720p, 1080p, 4k Video resolution.
- `generate_audio`: boolean No true - Whether to generate audio.
- `negative_prompt`: string No - The negative prompt for the generation.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation.

### Google Veo3.1 Reference To Video

- **Model ID:** `google/veo3.1/reference-to-video`
- **Operation:** `reference_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/google/veo3.1/reference-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/google/google-veo3.1-reference-to-video

**Request Parameters**

- `images`: array Yes [] 1 ~ 3 items The model will use the provided images as references to generate a video with consistent subjects. For fields that accept images: Accepts 1 to 3 images; Images Assets can be provided via URLs or Base64 encode; You m
- `prompt`: string Yes - The positive prompt for the generation.
- `resolution`: string No 1080p 720p, 1080p, 4k Video resolution.
- `generate_audio`: boolean No true - Whether to generate audio.
- `negative_prompt`: string No - The negative prompt for the generation.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation.

### Google Veo3.1 Text To Video

- **Model ID:** `google/veo3.1/text-to-video`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/google/veo3.1/text-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/google/google-veo3.1-text-to-video

**Request Parameters**

- `prompt`: string Yes - Text prompt for generation; Positive text prompt.
- `aspect_ratio`: string No 16:9 16:9, 9:16 Aspect ratio of the video.
- `duration`: integer No 8 8, 4, 6 The duration of the generated media in seconds.
- `resolution`: string No 1080p 720p, 1080p, 4k Video resolution.
- `generate_audio`: boolean No true - Whether to generate audio.
- `negative_prompt`: string No - Negative prompt for the generation.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation.

### Google Veo3.1 Video Extend

- **Model ID:** `google/veo3.1/video-extend`
- **Operation:** `video_extend`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/google/veo3.1/video-extend`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/google/google-veo3.1-video-extend

**Request Parameters**

- `video`: string Yes - The video to use for the generation.
- `prompt`: string No - The positive prompt for the generation.
- `resolution`: string No 1080p 720p, 1080p Video resolution.
- `negative_prompt`: string No - The negative prompt for the generation.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation.

### Google Veo3 Image To Video

- **Model ID:** `google/veo3/image-to-video`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/google/veo3/image-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/google/google-veo3-image-to-video

**Request Parameters**

- `image`: string Yes - The image to use for the generation.
- `prompt`: string Yes - The positive prompt for the generation.
- `aspect_ratio`: string No 16:9 16:9, 9:16 The aspect ratio of the generated media.
- `duration`: integer No 8 8, 4, 6 The duration of the generated media in seconds.
- `resolution`: string No 720p 720p, 1080p Video resolution.
- `generate_audio`: boolean No true - Whether to generate audio.
- `negative_prompt`: string No - The negative prompt for the generation.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation.

### Heygen Video Translate

- **Model ID:** `heygen/video-translate`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/heygen/video-translate`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/heygen/heygen-video-translate

**Request Parameters**

- `video`: string Yes - The video to translate.
- `output_language`: string No English English, Spanish, French, Hindi, Italian, German, Polish, Portuguese, Chinese, Japanese, Dutch, Turkish, Korean, Danish, Arabic, Romanian, Mandarin, Filipino, Swedish, Indonesian, Ukrainian, Greek, Czech, Bulgarian, Malay,

### Higgsfield Dop Image To Video

- **Model ID:** `higgsfield/dop/image-to-video`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/higgsfield/dop/image-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/higgsfield/higgsfield-dop-image-to-video

**Request Parameters**

- `image`: string Yes -
- `prompt`: string Yes - The prompt for generating the image.
- `end_image`: string No - -
- `motions`: array Yes [{"motion":"Zoom Out","strength":1}] 1 ~ 2 items Array of terminoogies to use for translation
- `options`: string No dop-turbo dop-lite, dop-turbo, dop-preview

### Kwaivgi Kling V1.6 I2V Pro

- **Model ID:** `kwaivgi/kling-v1.6-i2v-pro`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/kwaivgi/kling-v1.6-i2v-pro`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/kwaivgi/kwaivgi-kling-v1.6-i2v-pro

**Request Parameters**

- `image`: string Yes - First frame of the video; Supported image formats include.jpg/.jpeg/.png; The image file size cannot exceed 10MB, and the image resolution should not be less than 300*300px, and the aspect ratio of the image should be between 1
- `prompt`: string Yes - The positive prompt for the generation. max length 2500
- `negative_prompt`: string No - The negative prompt for the generation.
- `end_image`: string No - - Tail frame of the video; Supported image formats include.jpg/.jpeg/.png; The image file size cannot exceed 10MB, and the image resolution should not be less than 300*300px.
- `guidance_scale`: number No 0.5 0.00 ~ 1.00 The guidance scale to use for the generation.
- `duration`: integer No 5 5, 10 The duration of the generated media in seconds.

### Kwaivgi Kling V1.6 I2V Standard

- **Model ID:** `kwaivgi/kling-v1.6-i2v-standard`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/kwaivgi/kling-v1.6-i2v-standard`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/kwaivgi/kwaivgi-kling-v1.6-i2v-standard

**Request Parameters**

- `image`: string Yes - First frame of the video; Supported image formats include.jpg/.jpeg/.png; The image file size cannot exceed 10MB, and the image resolution should not be less than 300*300px.
- `prompt`: string Yes - The positive prompt for the generation. max length 2500
- `negative_prompt`: string No - The negative prompt for the generation.
- `guidance_scale`: number No 0.5 0.00 ~ 1.00 The guidance scale to use for the generation.
- `duration`: integer No 5 5, 10 The duration of the generated media in seconds.

### Kwaivgi Kling V1.6 Multi I2V Pro

- **Model ID:** `kwaivgi/kling-v1.6-multi-i2v-pro`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/kwaivgi/kling-v1.6-multi-i2v-pro`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/kwaivgi/kwaivgi-kling-v1.6-multi-i2v-pro

**Request Parameters**

- `images`: array Yes [] 1 ~ 4 items A list of images to use as style references.
- `prompt`: string Yes - The positive prompt for the generation. max length 2500
- `negative_prompt`: string No - The negative prompt for the generation.
- `duration`: integer No 5 5, 10 The duration of the generated media in seconds.
- `aspect_ratio`: string No 1:1 1:1, 16:9, 9:16 The aspect ratio of the generated media.

### Kwaivgi Kling V1.6 Multi I2V Standard

- **Model ID:** `kwaivgi/kling-v1.6-multi-i2v-standard`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/kwaivgi/kling-v1.6-multi-i2v-standard`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/kwaivgi/kwaivgi-kling-v1.6-multi-i2v-standard

**Request Parameters**

- `images`: array Yes [] 1 ~ 4 items A list of images to use as style references.
- `prompt`: string Yes - The positive prompt for the generation. max length 2500
- `negative_prompt`: string No - The negative prompt for the generation.
- `duration`: integer No 5 5, 10 The duration of the generated media in seconds.
- `aspect_ratio`: string No 1:1 1:1, 16:9, 9:16 The aspect ratio of the generated media.

### Kwaivgi Kling V1.6 T2V Standard

- **Model ID:** `kwaivgi/kling-v1.6-t2v-standard`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/kwaivgi/kling-v1.6-t2v-standard`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/kwaivgi/kwaivgi-kling-v1.6-t2v-standard

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation. max length 2500
- `aspect_ratio`: string No 16:9 16:9, 9:16, 1:1 The aspect ratio of the generated media.
- `negative_prompt`: string No - The negative prompt for the generation.
- `guidance_scale`: number No 0.5 0.00 ~ 1.00 The guidance scale to use for the generation.
- `duration`: integer No 5 5, 10 The duration of the generated media in seconds.

### Kwaivgi Kling V2.0 I2V Master

- **Model ID:** `kwaivgi/kling-v2.0-i2v-master`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/kwaivgi/kling-v2.0-i2v-master`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/kwaivgi/kwaivgi-kling-v2.0-i2v-master

**Request Parameters**

- `image`: string Yes - First frame of the video; Supported image formats include.jpg/.jpeg/.png; The image file size cannot exceed 10MB, and the image resolution should not be less than 300*300px, and the aspect ratio of the image should be between 1
- `end_image`: string No - - Tail frame of the video; Supported image formats include.jpg/.jpeg/.png; The image file size cannot exceed 10MB, and the image resolution should not be less than 300*300px.
- `prompt`: string Yes - The positive prompt for the generation. max length 2500
- `negative_prompt`: string No - The negative prompt for the generation.
- `guidance_scale`: number No 0.5 0.00 ~ 1.00 The guidance scale to use for the generation.
- `duration`: integer No 5 5, 10 The duration of the generated media in seconds.

### Kwaivgi Kling V2.0 T2V Master

- **Model ID:** `kwaivgi/kling-v2.0-t2v-master`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/kwaivgi/kling-v2.0-t2v-master`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/kwaivgi/kwaivgi-kling-v2.0-t2v-master

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation. max length 2500
- `aspect_ratio`: string No 16:9 16:9, 9:16, 1:1 The aspect ratio of the generated media.
- `negative_prompt`: string No - The negative prompt for the generation.
- `duration`: integer No 5 5, 10 The duration of the generated media in seconds.

### Kwaivgi Kling V2.1 I2V Master

- **Model ID:** `kwaivgi/kling-v2.1-i2v-master`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/kwaivgi/kling-v2.1-i2v-master`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/kwaivgi/kwaivgi-kling-v2.1-i2v-master

**Request Parameters**

- `image`: string Yes - First frame of the video; Supported image formats include.jpg/.jpeg/.png; The image file size cannot exceed 10MB, and the image resolution should not be less than 300*300px.
- `prompt`: string Yes - The positive prompt for the generation.max length 2500
- `negative_prompt`: string No - The negative prompt for the generation.
- `guidance_scale`: number No 0.5 0.00 ~ 1.00 The guidance scale to use for the generation.
- `duration`: integer No 5 5, 10 The duration of the generated media in seconds.

### Kwaivgi Kling V2.1 I2V Pro

- **Model ID:** `kwaivgi/kling-v2.1-i2v-pro`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/kwaivgi/kling-v2.1-i2v-pro`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/kwaivgi/kwaivgi-kling-v2.1-i2v-pro

**Request Parameters**

- `image`: string Yes - First frame of the video; Supported image formats include.jpg/.jpeg/.png; The image file size cannot exceed 10MB, and the image resolution should not be less than 300*300px.
- `prompt`: string Yes - The positive prompt for the generation. max length 2500
- `negative_prompt`: string No - The negative prompt for the generation.
- `guidance_scale`: number No 0.5 0.00 ~ 1.00 The guidance scale to use for the generation.
- `duration`: integer No 5 5, 10 The duration of the generated media in seconds.

### Kwaivgi Kling V2.1 I2V Pro Start End Frame

- **Model ID:** `kwaivgi/kling-v2.1-i2v-pro/start-end-frame`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/kwaivgi/kling-v2.1-i2v-pro/start-end-frame`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/kwaivgi/kwaivgi-kling-v2.1-i2v-pro-start-end-frame

**Request Parameters**

- `image`: string Yes - First frame of the video; Supported image formats include.jpg/.jpeg/.png; The image file size cannot exceed 10MB, and the image resolution should not be less than 300*300px.
- `prompt`: string Yes - The positive prompt for the generation. max length 2500
- `negative_prompt`: string No - The negative prompt for the generation.
- `end_image`: string Yes - - Last frame of the video; Supported image formats include.jpg/.jpeg/.png; The image file size cannot exceed 10MB, and the image resolution should not be less than 300*300px.
- `guidance_scale`: number No 0.5 0.00 ~ 1.00 The guidance scale to use for the generation.
- `duration`: integer No 5 5, 10 The duration of the generated media in seconds.

### Kwaivgi Kling V2.1 I2V Standard

- **Model ID:** `kwaivgi/kling-v2.1-i2v-standard`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/kwaivgi/kling-v2.1-i2v-standard`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/kwaivgi/kwaivgi-kling-v2.1-i2v-standard

**Request Parameters**

- `image`: string Yes - First frame of the video; Supported image formats include.jpg/.jpeg/.png; The image file size cannot exceed 10MB, and the image resolution should not be less than 300*300px.
- `prompt`: string Yes - The positive prompt for the generation. max length 2500
- `negative_prompt`: string No - The negative prompt for the generation.
- `guidance_scale`: number No 0.5 0.00 ~ 1.00 The guidance scale to use for the generation.
- `duration`: integer No 5 5, 10 The duration of the generated media in seconds.

### Kwaivgi Kling V2.1 T2V Master

- **Model ID:** `kwaivgi/kling-v2.1-t2v-master`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/kwaivgi/kling-v2.1-t2v-master`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/kwaivgi/kwaivgi-kling-v2.1-t2v-master

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `negative_prompt`: string No - The positive prompt for the generation.
- `aspect_ratio`: string No 16:9 16:9, 9:16, 1:1 The aspect ratio of the generated media.
- `duration`: integer No 5 5, 10 The duration of the generated media in seconds.
- `guidance_scale`: number No 0.5 0.0 ~ 1.0 The guidance scale to use for the generation.

### Kwaivgi Kling V2.5 Turbo Pro Image To Video

- **Model ID:** `kwaivgi/kling-v2.5-turbo-pro/image-to-video`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/kwaivgi/kling-v2.5-turbo-pro/image-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/kwaivgi/kwaivgi-kling-v2.5-turbo-pro-image-to-video

**Request Parameters**

- `image`: string Yes - First frame of the video; Supported image formats include.jpg/.jpeg/.png; The image file size cannot exceed 10MB, and the image resolution should not be less than 300*300px, and the aspect ratio of the image should be between 1
- `last_image`: string No - - The end image for generating the output.
- `prompt`: string Yes - The positive prompt for the generation. max length 2500
- `negative_prompt`: string No - The negative prompt for the generation.
- `guidance_scale`: number No 0.5 0.00 ~ 1.00 The guidance scale to use for the generation.
- `duration`: integer No 5 5, 10 The duration of the generated media in seconds.

### Kwaivgi Kling V2.5 Turbo Pro Text To Video

- **Model ID:** `kwaivgi/kling-v2.5-turbo-pro/text-to-video`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/kwaivgi/kling-v2.5-turbo-pro/text-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/kwaivgi/kwaivgi-kling-v2.5-turbo-pro-text-to-video

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation. max length 2500
- `negative_prompt`: string No - The negative prompt for the generation.
- `aspect_ratio`: string No 16:9 1:1, 9:16, 16:9 The aspect ratio of the generated media.
- `duration`: integer No 5 5, 10 The duration of the generated media in seconds.
- `guidance_scale`: number No 0.5 0.00 ~ 1.00 The guidance scale to use for the generation.

### Kwaivgi Kling V2.5 Turbo Std Image To Video

- **Model ID:** `kwaivgi/kling-v2.5-turbo-std/image-to-video`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/kwaivgi/kling-v2.5-turbo-std/image-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/kwaivgi/kwaivgi-kling-v2.5-turbo-std-image-to-video

**Request Parameters**

- `image`: string Yes - First frame of the video; Supported image formats include.jpg/.jpeg/.png; The image file size cannot exceed 10MB, and the image resolution should not be less than 300*300px, and the aspect ratio of the image should be between 1
- `prompt`: string Yes - The positive prompt for the generation.
- `negative_prompt`: string No - The negative prompt for the generation.
- `guidance_scale`: number No 0.5 0.00 ~ 1.00 The guidance scale to use for the generation.
- `duration`: integer No 5 5, 10 The duration of the generated media in seconds.

### Kwaivgi Kling V2.6 Pro Image To Video

- **Model ID:** `kwaivgi/kling-v2.6-pro/image-to-video`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/kwaivgi/kling-v2.6-pro/image-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/kwaivgi/kwaivgi-kling-v2.6-pro-image-to-video

**Request Parameters**

- `image`: string Yes - Supported image formats:.jpg /.jpeg /.png The size of the image file should not exceed 10MB, the width and height of the image should be no less than 300px, and the aspect ratio of the image should be between 1:2.5 and 2.5:1
- `prompt`: string Yes - The positive prompt for the generation.
- `negative_prompt`: string No - The negative prompt for the generation.
- `end_image`: string No - - URL of the ending image.
- `cfg_scale`: number No 0.5 0.00 ~ 1.00 Flexibility in video generation; The higher the value, the lower the model’s degree of flexibility, and the stronger the relevance to the user’s prompt.
- `sound`: boolean No - - Whether sound is generated simultaneously when generating a video
- `voice_list`: array No - - List of tones referenced when generating videos. When the voice_id parameter is not empty the voice ID, the video generation task will be billed based on the with voice generation” metric.
- `duration`: integer No 5 5, 10 The duration of the generated media in seconds.

### Kwaivgi Kling V2.6 Pro Text To Video

- **Model ID:** `kwaivgi/kling-v2.6-pro/text-to-video`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/kwaivgi/kling-v2.6-pro/text-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/kwaivgi/kwaivgi-kling-v2.6-pro-text-to-video

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `negative_prompt`: string No - The negative prompt for the generation.
- `cfg_scale`: number No 0.5 0.00 ~ 1.00 Flexibility in video generation; The higher the value, the lower the model’s degree of flexibility, and the stronger the relevance to the user’s prompt.
- `sound`: boolean No true - Whether sound is generated simultaneously when generating a video
- `aspect_ratio`: string No 1:1 1:1, 9:16, 16:9 The aspect ratio of the generated media.
- `duration`: integer No 5 5, 10 The duration of the generated media in seconds.

### Kwaivgi Kling V2.6 Std Image To Video

- **Model ID:** `kwaivgi/kling-v2.6-std/image-to-video`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/kwaivgi/kling-v2.6-std/image-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/kwaivgi/kwaivgi-kling-v2.6-std-image-to-video

**Request Parameters**

- `image`: string Yes - Supported image formats: .jpg/.jpeg/.png. The size should not exceed 10MB, width and height should be no less than 300px.
- `prompt`: string Yes - The positive prompt for the generation.
- `negative_prompt`: string No - The negative prompt for the generation.
- `duration`: integer No 5 5, 10 The duration of the generated media in seconds.

### Kwaivgi Kling V2.6 Std Text To Video

- **Model ID:** `kwaivgi/kling-v2.6-std/text-to-video`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/kwaivgi/kling-v2.6-std/text-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/kwaivgi/kwaivgi-kling-v2.6-std-text-to-video

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `negative_prompt`: string No - The negative prompt for the generation.
- `aspect_ratio`: string No 16:9 1:1, 9:16, 16:9 The aspect ratio of the generated media.
- `duration`: integer No 5 5, 10 The duration of the generated media in seconds.

### Kwaivgi Kling V3.0 4k Image To Video

- **Model ID:** `kwaivgi/kling-v3.0-4k/image-to-video`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/kwaivgi/kling-v3.0-4k/image-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/kwaivgi/kwaivgi-kling-v3.0-4k-image-to-video

**Request Parameters**

- `image`: string Yes - Supported image formats: .jpg/.jpeg/.png. The size of the image file should not exceed 10MB, the width and height of the image should be no less than 300px, and the aspect ratio of the image should be between 1:2.5 and 2.5:1.
- `prompt`: string No - Text prompt for video generation. Either prompt or multi_prompt must be provided, but not both. Specify a element, image, or video in the format of<<<>>, such as<<element_1>>>,<<<image_1>>>,<<<video_1>>>.
- `negative_prompt`: string No - The negative prompt for the generation.
- `end_image`: string No - - URL of the ending image. multi_shot is not supported with end image.
- `duration`: integer No 5 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15 The duration of the generated media in seconds.
- `cfg_scale`: number No 0.5 0.00 ~ 1.00 Flexibility in video generation; The higher the value, the lower the model's degree of flexibility, and the stronger the relevance to the user's prompt.
- `sound`: boolean No - - Whether sound is generated simultaneously when generating a video.
- `shot_type`: string No customize customize, intelligent Shot type for the generation.
- `multi_prompt`: array No - - List of prompts for multi-shot video generation. If provided, divides the video into multiple shots.
- `element_list`: array No - - Element reference list. To get available elements and their IDs, visit: https://wavespeed.ai/models/kwaivgi/kling-elements

### Kwaivgi Kling V3.0 4k Text To Video

- **Model ID:** `kwaivgi/kling-v3.0-4k/text-to-video`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/kwaivgi/kling-v3.0-4k/text-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/kwaivgi/kwaivgi-kling-v3.0-4k-text-to-video

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `negative_prompt`: string No - The negative prompt for the generation.
- `duration`: integer No 5 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15 The duration of the generated media in seconds.
- `aspect_ratio`: string No 16:9 16:9, 9:16, 1:1 The aspect ratio of the generated video.
- `cfg_scale`: number No 0.5 0.00 ~ 1.00 Flexibility in video generation; The higher the value, the lower the model's degree of flexibility, and the stronger the relevance to the user's prompt.
- `sound`: boolean No - - Whether sound is generated simultaneously when generating a video.
- `shot_type`: string No customize customize, intelligent Shot type for the generation.
- `multi_prompt`: array No - - List of multi-prompt elements for the generation.
- `element_list`: array No - - Element reference list.

### Kwaivgi Kling V3.0 Pro Image To Video

- **Model ID:** `kwaivgi/kling-v3.0-pro/image-to-video`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/kwaivgi/kling-v3.0-pro/image-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/kwaivgi/kwaivgi-kling-v3.0-pro-image-to-video

**Request Parameters**

- `image`: string Yes - Supported image formats: .jpg/.jpeg/.png. The size of the image file should not exceed 10MB, the width and height of the image should be no less than 300px, and the aspect ratio of the image should be between 1:2.5 and 2.5:1.
- `prompt`: string No - Text prompt for video generation. Either prompt or multi_prompt must be provided, but not both. Specify a element, image, or video in the format of<<<>>, such as<<element_1>>>,<<<image_1>>>,<<<video_1>>>.
- `negative_prompt`: string No - The negative prompt for the generation.
- `end_image`: string No - - URL of the ending image. multi_shot is not supported with end image.
- `duration`: integer No 5 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15 The duration of the generated media in seconds.
- `cfg_scale`: number No 0.5 0.00 ~ 1.00 Flexibility in video generation; The higher the value, the lower the model's degree of flexibility, and the stronger the relevance to the user's prompt.
- `sound`: boolean No - - Whether sound is generated simultaneously when generating a video.
- `shot_type`: string No customize customize, intelligent Shot type for the generation.
- `multi_prompt`: array No - - List of prompts for multi-shot video generation. If provided, divides the video into multiple shots.
- `element_list`: array No - - Element reference list. To get available elements and their IDs, visit: https://wavespeed.ai/models/kwaivgi/kling-elements

### Kwaivgi Kling V3.0 Pro Text To Video

- **Model ID:** `kwaivgi/kling-v3.0-pro/text-to-video`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/kwaivgi/kling-v3.0-pro/text-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/kwaivgi/kwaivgi-kling-v3.0-pro-text-to-video

**Request Parameters**

- `prompt`: string No - The positive prompt for the generation.
- `negative_prompt`: string No - The negative prompt for the generation.
- `duration`: integer No 5 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15 The duration of the generated media in seconds.
- `aspect_ratio`: string No 16:9 16:9, 9:16, 1:1 The aspect ratio of the generated video.
- `cfg_scale`: number No 0.5 0.00 ~ 1.00 Flexibility in video generation; The higher the value, the lower the model's degree of flexibility, and the stronger the relevance to the user's prompt.
- `sound`: boolean No - - Whether sound is generated simultaneously when generating a video.
- `shot_type`: string No customize customize, intelligent Shot type for the generation.
- `multi_prompt`: array No - - List of multi-prompt elements for the generation.
- `element_list`: array No - - Element reference list.

### Kwaivgi Kling V3.0 Std Image To Video

- **Model ID:** `kwaivgi/kling-v3.0-std/image-to-video`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/kwaivgi/kling-v3.0-std/image-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/kwaivgi/kwaivgi-kling-v3.0-std-image-to-video

**Request Parameters**

- `image`: string Yes - Supported image formats: .jpg/.jpeg/.png. The size of the image file should not exceed 10MB, the width and height of the image should be no less than 300px, and the aspect ratio of the image should be between 1:2.5 and 2.5:1.
- `prompt`: string No - Text prompt for video generation. Either prompt or multi_prompt must be provided, but not both.
- `negative_prompt`: string No - The negative prompt for the generation.
- `end_image`: string No - - URL of the ending image. multi_shot is not supported with end image.
- `duration`: integer No 5 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15 The duration of the generated media in seconds.
- `cfg_scale`: number No 0.5 0.00 ~ 1.00 Flexibility in video generation; The higher the value, the lower the model's degree of flexibility, and the stronger the relevance to the user's prompt.
- `sound`: boolean No - - Whether sound is generated simultaneously when generating a video.
- `shot_type`: string No customize customize, intelligent Shot type for the generation.
- `multi_prompt`: array No - - List of prompts for multi-shot video generation. If provided, divides the video into multiple shots.
- `element_list`: array No - - Element reference list.

### Kwaivgi Kling V3.0 Std Text To Video

- **Model ID:** `kwaivgi/kling-v3.0-std/text-to-video`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/kwaivgi/kling-v3.0-std/text-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/kwaivgi/kwaivgi-kling-v3.0-std-text-to-video

**Request Parameters**

- `prompt`: string No - The positive prompt for the generation.
- `negative_prompt`: string No - The negative prompt for the generation.
- `duration`: integer No 5 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15 The duration of the generated media in seconds.
- `aspect_ratio`: string No 16:9 16:9, 9:16, 1:1 The aspect ratio of the generated video.
- `cfg_scale`: number No 0.5 0.00 ~ 1.00 Flexibility in video generation; The higher the value, the lower the model's degree of flexibility, and the stronger the relevance to the user's prompt.
- `sound`: boolean No - - Whether sound is generated simultaneously when generating a video.
- `shot_type`: string No customize customize, intelligent Shot type for the generation.
- `multi_prompt`: array No - - List of multi-prompt elements for the generation.
- `element_list`: array No - - Element reference list.

### Kwaivgi Kling Video O1 Std Image To Video

- **Model ID:** `kwaivgi/kling-video-o1-std/image-to-video`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/kwaivgi/kling-video-o1-std/image-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/kwaivgi/kwaivgi-kling-video-o1-std-image-to-video

**Request Parameters**

- `image`: string Yes - first_frame is the first frame
- `prompt`: string Yes - The positive prompt for the generation.
- `last_image`: string No - - last_frame is the last frame.
- `duration`: integer No 5 3, 4, 5, 6, 7, 8, 9, 10 The duration of the generated media. Only 5s or 10s are supported when last_image is not used.

### Kwaivgi Kling Video O1 Std Reference To Video

- **Model ID:** `kwaivgi/kling-video-o1-std/reference-to-video`
- **Operation:** `reference_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/kwaivgi/kling-video-o1-std/reference-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/kwaivgi/kwaivgi-kling-video-o1-std-reference-to-video

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `video`: string No - The video URL.
- `images`: array No [] - Including reference images of the element, scene, style, etc. Max 10
- `keep_original_sound`: boolean No true - Select whether to keep the video original sound through the parameter
- `duration`: integer No 5 3, 4, 5, 6, 7, 8, 9, 10 The duration of the generated media in seconds.

### Kwaivgi Kling Video O1 Std Text To Video

- **Model ID:** `kwaivgi/kling-video-o1-std/text-to-video`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/kwaivgi/kling-video-o1-std/text-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/kwaivgi/kwaivgi-kling-video-o1-std-text-to-video

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `aspect_ratio`: string No 16:9 16:9, 9:16, 1:1 The aspect ratio of the generated video.
- `duration`: integer No 5 5, 10 The duration of the generated media in seconds.

### Kwaivgi Kling Video O1 Std Video Edit

- **Model ID:** `kwaivgi/kling-video-o1-std/video-edit`
- **Operation:** `video_edit`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/kwaivgi/kling-video-o1-std/video-edit`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/kwaivgi/kwaivgi-kling-video-o1-std-video-edit

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `video`: string Yes - The video URL.
- `images`: array No [] - Including reference images of the element, scene, style, etc. Max 10
- `keep_original_sound`: boolean No true - Select whether to keep the video original sound through the parameter

### Kwaivgi Kling Video O1 Image To Video

- **Model ID:** `kwaivgi/kling-video-o1/image-to-video`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/kwaivgi/kling-video-o1/image-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/kwaivgi/kwaivgi-kling-video-o1-image-to-video

**Request Parameters**

- `image`: string Yes - first_frame is the first frame
- `prompt`: string Yes - The positive prompt for the generation.
- `last_image`: string No - - last_frame is the last frame.
- `duration`: integer No 5 3, 4, 5, 6, 7, 8, 9, 10 The duration of the generated media. Only 5s or 10s are supported when last_image is not used.

### Kwaivgi Kling Video O1 Reference To Video

- **Model ID:** `kwaivgi/kling-video-o1/reference-to-video`
- **Operation:** `reference_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/kwaivgi/kling-video-o1/reference-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/kwaivgi/kwaivgi-kling-video-o1-reference-to-video

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `video`: string No - The video URL.
- `images`: array No [] - With a reference video: image elements ≤ 4; without a reference video: ≤ 7
- `keep_original_sound`: boolean No true - Select whether to keep the video original sound through the parameter
- `aspect_ratio`: string No 16:9 16:9, 9:16, 1:1 The aspect ratio of the generated video.
- `duration`: integer No 5 3, 4, 5, 6, 7, 8, 9, 10 The duration of the generated media in seconds.

### Kwaivgi Kling Video O1 Text To Video

- **Model ID:** `kwaivgi/kling-video-o1/text-to-video`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/kwaivgi/kling-video-o1/text-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/kwaivgi/kwaivgi-kling-video-o1-text-to-video

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `aspect_ratio`: string No 16:9 16:9, 9:16, 1:1 The aspect ratio of the generated video.
- `duration`: integer No 5 5, 10 The duration of the generated media in seconds.

### Kwaivgi Kling Video O1 Video Edit

- **Model ID:** `kwaivgi/kling-video-o1/video-edit`
- **Operation:** `video_edit`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/kwaivgi/kling-video-o1/video-edit`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/kwaivgi/kwaivgi-kling-video-o1-video-edit

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `video`: string Yes - The video URL.
- `images`: array No [] - Including reference images of the element, scene, style, etc. Max 4
- `keep_original_sound`: boolean No true - Select whether to keep the video original sound through the parameter

### Kwaivgi Kling Video O1 Video Edit Fast

- **Model ID:** `kwaivgi/kling-video-o1/video-edit-fast`
- **Operation:** `video_edit`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/kwaivgi/kling-video-o1/video-edit-fast`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/kwaivgi/kwaivgi-kling-video-o1-video-edit-fast

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `video`: string Yes - The video URL.
- `images`: array No [] - Including reference images of the element, scene, style, etc. Max 4
- `keep_original_sound`: boolean No true - Select whether to keep the video original sound through the parameter
- `aspect_ratio`: string No - 16:9, 9:16, 1:1 The aspect ratio of the generated video.

### Kwaivgi Kling Video O3 4k Image To Video

- **Model ID:** `kwaivgi/kling-video-o3-4k/image-to-video`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/kwaivgi/kling-video-o3-4k/image-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/kwaivgi/kwaivgi-kling-video-o3-4k-image-to-video

**Request Parameters**

- `image`: string Yes - The first frame image URL.
- `prompt`: string No - The positive prompt for the generation.
- `end_image`: string No - - The last frame image URL.
- `duration`: integer No 5 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15 The duration of the generated media in seconds (3-15).
- `sound`: boolean No false - Whether to generate audio for the video.
- `shot_type`: string No customize customize, intelligent Shot type for the generation.
- `multi_prompt`: array No - - List of multi-prompt elements for the generation.
- `element_list`: array No - - Element reference list.

### Kwaivgi Kling Video O3 4k Reference To Video

- **Model ID:** `kwaivgi/kling-video-o3-4k/reference-to-video`
- **Operation:** `reference_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/kwaivgi/kling-video-o3-4k/reference-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/kwaivgi/kwaivgi-kling-video-o3-4k-reference-to-video

**Request Parameters**

- `prompt`: string No - The positive prompt for the generation.
- `images`: array No [] - Reference images. With a reference video: image elements ≤ 4; without a reference video: ≤ 7
- `sound`: boolean No false - Whether to generate audio for the video.
- `aspect_ratio`: string No 16:9 16:9, 9:16, 1:1 The aspect ratio of the generated video.
- `duration`: integer No 5 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15 The duration of the generated media in seconds (3-15).
- `shot_type`: string No customize customize, intelligent Shot type for the generation.
- `multi_prompt`: array No - - List of multi-prompt elements for the generation.
- `element_list`: array No - - Element reference list.

### Kwaivgi Kling Video O3 4k Text To Video

- **Model ID:** `kwaivgi/kling-video-o3-4k/text-to-video`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/kwaivgi/kling-video-o3-4k/text-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/kwaivgi/kwaivgi-kling-video-o3-4k-text-to-video

**Request Parameters**

- `prompt`: string No - The positive prompt for the generation.
- `aspect_ratio`: string No 16:9 16:9, 9:16, 1:1 The aspect ratio of the generated video.
- `duration`: integer No 5 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15 The duration of the generated media in seconds (3-15).
- `sound`: boolean No false - Whether to generate audio for the video.
- `shot_type`: string No customize customize, intelligent Shot type for the generation.
- `multi_prompt`: array No - - List of multi-prompt elements for the generation.
- `element_list`: array No - - Element reference list.

### Kwaivgi Kling Video O3 Pro Image To Video

- **Model ID:** `kwaivgi/kling-video-o3-pro/image-to-video`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/kwaivgi/kling-video-o3-pro/image-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/kwaivgi/kwaivgi-kling-video-o3-pro-image-to-video

**Request Parameters**

- `image`: string Yes - The first frame image URL.
- `prompt`: string No - The positive prompt for the generation.
- `end_image`: string No - - The last frame image URL.
- `duration`: integer No 5 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15 The duration of the generated media in seconds (3-15).
- `sound`: boolean No false - Whether to generate audio for the video.
- `shot_type`: string No customize customize, intelligent Shot type for the generation.
- `multi_prompt`: array No - - List of multi-prompt elements for the generation.
- `element_list`: array No - - Element reference list.

### Kwaivgi Kling Video O3 Pro Reference To Video

- **Model ID:** `kwaivgi/kling-video-o3-pro/reference-to-video`
- **Operation:** `reference_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/kwaivgi/kling-video-o3-pro/reference-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/kwaivgi/kwaivgi-kling-video-o3-pro-reference-to-video

**Request Parameters**

- `prompt`: string No - The positive prompt for the generation.
- `video`: string No - The reference video URL.
- `images`: array No [] - Reference images. With a reference video: image elements ≤ 4; without a reference video: ≤ 7
- `keep_original_sound`: boolean No true - Whether to keep the original sound from the reference video.
- `sound`: boolean No false - Whether to generate audio for the video.
- `aspect_ratio`: string No 16:9 16:9, 9:16, 1:1 The aspect ratio of the generated video.
- `duration`: integer No 5 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15 The duration of the generated media in seconds (3-15).
- `shot_type`: string No customize customize, intelligent Shot type for the generation.
- `multi_prompt`: array No - - List of multi-prompt elements for the generation.
- `element_list`: array No - - Element reference list.

### Kwaivgi Kling Video O3 Pro Text To Video

- **Model ID:** `kwaivgi/kling-video-o3-pro/text-to-video`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/kwaivgi/kling-video-o3-pro/text-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/kwaivgi/kwaivgi-kling-video-o3-pro-text-to-video

**Request Parameters**

- `prompt`: string No - The positive prompt for the generation.
- `aspect_ratio`: string No 16:9 16:9, 9:16, 1:1 The aspect ratio of the generated video.
- `duration`: integer No 5 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15 The duration of the generated media in seconds (3-15).
- `sound`: boolean No false - Whether to generate audio for the video.
- `shot_type`: string No customize customize, intelligent Shot type for the generation.
- `multi_prompt`: array No - - List of multi-prompt elements for the generation.
- `element_list`: array No - - Element reference list.

### Kwaivgi Kling Video O3 Pro Video Edit

- **Model ID:** `kwaivgi/kling-video-o3-pro/video-edit`
- **Operation:** `video_edit`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/kwaivgi/kling-video-o3-pro/video-edit`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/kwaivgi/kwaivgi-kling-video-o3-pro-video-edit

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `video`: string Yes - The video URL. Video duration can not be longer than 10s.
- `images`: array No [] - Including reference images of the element, scene, style, etc. Max 4
- `keep_original_sound`: boolean No true - Whether to keep the original sound from the video.
- `shot_type`: string No customize customize, intelligent Shot type for the generation.
- `element_list`: array No - - Element reference list.

### Kwaivgi Kling Video O3 Std Image To Video

- **Model ID:** `kwaivgi/kling-video-o3-std/image-to-video`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/kwaivgi/kling-video-o3-std/image-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/kwaivgi/kwaivgi-kling-video-o3-std-image-to-video

**Request Parameters**

- `image`: string Yes - The first frame image URL.
- `prompt`: string No - The positive prompt for the generation.
- `end_image`: string No - - The last frame image URL.
- `duration`: integer No 5 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15 The duration of the generated media in seconds (3-15).
- `sound`: boolean No false - Whether to generate audio for the video.
- `shot_type`: string No customize customize, intelligent Shot type for the generation.
- `multi_prompt`: array No - - List of multi-prompt elements for the generation.

### Kwaivgi Kling Video O3 Std Reference To Video

- **Model ID:** `kwaivgi/kling-video-o3-std/reference-to-video`
- **Operation:** `reference_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/kwaivgi/kling-video-o3-std/reference-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/kwaivgi/kwaivgi-kling-video-o3-std-reference-to-video

**Request Parameters**

- `prompt`: string No - The positive prompt for the generation.
- `video`: string No - The reference video URL.
- `images`: array No [] - Reference images. With a reference video: image elements ≤ 4; without a reference video: ≤ 7
- `keep_original_sound`: boolean No true - Whether to keep the original sound from the reference video.
- `sound`: boolean No false - Whether to generate audio for the video.
- `aspect_ratio`: string No 16:9 16:9, 9:16, 1:1 The aspect ratio of the generated video.
- `duration`: integer No 5 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15 The duration of the generated media in seconds (3-15).
- `shot_type`: string No customize customize, intelligent Shot type for the generation.
- `multi_prompt`: array No - - List of multi-prompt elements for the generation.

### Kwaivgi Kling Video O3 Std Text To Video

- **Model ID:** `kwaivgi/kling-video-o3-std/text-to-video`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/kwaivgi/kling-video-o3-std/text-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/kwaivgi/kwaivgi-kling-video-o3-std-text-to-video

**Request Parameters**

- `prompt`: string No - The positive prompt for the generation.
- `aspect_ratio`: string No 16:9 16:9, 9:16, 1:1 The aspect ratio of the generated video.
- `duration`: integer No 5 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15 The duration of the generated media in seconds (3-15).
- `sound`: boolean No false - Whether to generate audio for the video.
- `shot_type`: string No customize customize, intelligent Shot type for the generation.
- `multi_prompt`: array No - - List of multi-prompt elements for the generation.

### Kwaivgi Kling Video O3 Std Video Edit

- **Model ID:** `kwaivgi/kling-video-o3-std/video-edit`
- **Operation:** `video_edit`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/kwaivgi/kling-video-o3-std/video-edit`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/kwaivgi/kwaivgi-kling-video-o3-std-video-edit

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `video`: string Yes - The video URL. Video duration can not be longer than 10s.
- `images`: array No [] - Including reference images of the element, scene, style, etc. Max 4
- `keep_original_sound`: boolean No true - Whether to keep the original sound from the video.
- `shot_type`: string No customize customize, intelligent Shot type for the generation.

### Kwaivgi Kling Video To Audio

- **Model ID:** `kwaivgi/kling-video-to-audio`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/kwaivgi/kling-video-to-audio`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/kwaivgi/kwaivgi-kling-video-to-audio

**Request Parameters**

- `video`: string No - The video for generating the output.Please note that the duration cannot exceed 20s.
- `sound_effect_prompt`: string No - - Text prompt for sound effect generation, maximum 200 characters
- `bgm_prompt`: string No - - Text prompt for background music generation, maximum 200 characters
- `asmr_mode`: boolean No false - Enable ASMR mode to enhance detailed sound effects, suitable for immersive content scenarios

### Lightricks Ltx 2 Fast Image To Video

- **Model ID:** `lightricks/ltx-2-fast/image-to-video`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/lightricks/ltx-2-fast/image-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/lightricks/lightricks-ltx-2-fast-image-to-video

**Request Parameters**

- `image`: string Yes - The image for the generation.
- `prompt`: string Yes - The positive prompt for the generation.
- `duration`: integer No 6 6, 8, 10, 12, 14, 16, 18, 20 The duration of the generated media in seconds.
- `generate_audio`: boolean No true - Whether to generate audio.

### Lightricks Ltx 2 Fast Text To Video

- **Model ID:** `lightricks/ltx-2-fast/text-to-video`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/lightricks/ltx-2-fast/text-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/lightricks/lightricks-ltx-2-fast-text-to-video

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `duration`: integer No 6 6, 8, 10, 12, 14, 16, 18, 20 The duration of the generated media in seconds.
- `generate_audio`: boolean No true - Whether to generate audio.

### Lightricks Ltx 2 Pro Image To Video

- **Model ID:** `lightricks/ltx-2-pro/image-to-video`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/lightricks/ltx-2-pro/image-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/lightricks/lightricks-ltx-2-pro-image-to-video

**Request Parameters**

- `image`: string Yes - The image for the generation.
- `prompt`: string Yes - The positive prompt for the generation.
- `duration`: integer No 6 6, 8, 10 The duration of the generated media in seconds.
- `generate_audio`: boolean No true - Whether to generate audio.

### Lightricks Ltx 2 Pro Text To Video

- **Model ID:** `lightricks/ltx-2-pro/text-to-video`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/lightricks/ltx-2-pro/text-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/lightricks/lightricks-ltx-2-pro-text-to-video

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `duration`: integer No 6 6, 8, 10 The duration of the generated media in seconds.
- `generate_audio`: boolean No true - Whether to generate audio.

### Luma Modify Video

- **Model ID:** `luma/modify-video`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/luma/modify-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/luma/luma-modify-video

**Request Parameters**

- `video`: string Yes - The source video to modify. Maximum file size is 100MB and maximum duration is 30 seconds.
- `prompt`: string No - Text instruction to guide how the video should be modified or restyled.
- `mode`: string No adhere_1 adhere_1, adhere_2, adhere_3, flex_1, flex_2, flex_3, reimagine_1, reimagine_2, reimagine_3 Controls how closely the output follows the source video. Adhere: subtle enhancements staying very close to original. Flex: allow
- `first_frame`: string No - - Optional modified version of the original first frame to guide the video transformation.

### Luma Ray 1.6 I2V

- **Model ID:** `luma/ray-1.6-i2v`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/luma/ray-1.6-i2v`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/luma/luma-ray-1.6-i2v

**Request Parameters**

- `image`: string Yes - The image for generating the output.
- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 1280*720 1280*720, 720*1280 The size of the generated media in pixels (width*height).
- `duration`: integer No 5 5, 10 The duration of the generated media in seconds.

### Luma Ray 1.6 T2V

- **Model ID:** `luma/ray-1.6-t2v`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/luma/ray-1.6-t2v`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/luma/luma-ray-1.6-t2v

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 1280*720 1280*720, 720*1280 The size of the generated media in pixels (width*height).
- `duration`: integer No 5 5, 10 The duration of the generated media in seconds.

### Luma Ray 2 Flash I2V

- **Model ID:** `luma/ray-2-flash-i2v`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/luma/ray-2-flash-i2v`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/luma/luma-ray-2-flash-i2v

**Request Parameters**

- `image`: string Yes - The image for generating the output.
- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 1280*720 1280*720, 720*1280 The size of the generated media in pixels (width*height).
- `duration`: integer No 5 5, 10 The duration of the generated media in seconds.

### Luma Ray 2 Flash T2V

- **Model ID:** `luma/ray-2-flash-t2v`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/luma/ray-2-flash-t2v`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/luma/luma-ray-2-flash-t2v

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 1280*720 1280*720, 720*1280 The size of the generated media in pixels (width*height).
- `duration`: integer No 5 5, 10 The duration of the generated media in seconds.

### Luma Ray 2 I2V

- **Model ID:** `luma/ray-2-i2v`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/luma/ray-2-i2v`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/luma/luma-ray-2-i2v

**Request Parameters**

- `image`: string Yes - The image for generating the output.
- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 1280*720 1280*720, 720*1280 The size of the generated media in pixels (width*height).
- `duration`: integer No 5 5, 10 The duration of the generated media in seconds.

### Luma Ray 2 T2V

- **Model ID:** `luma/ray-2-t2v`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/luma/ray-2-t2v`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/luma/luma-ray-2-t2v

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 1280*720 1280*720, 720*1280 The size of the generated media in pixels (width*height).
- `duration`: integer No 5 5, 10 The duration of the generated media in seconds.

### Midjourney Image To Video

- **Model ID:** `midjourney/image-to-video`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/midjourney/image-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/midjourney/midjourney-image-to-video

**Request Parameters**

- `image`: string Yes - The image for generating the output.
- `prompt`: string Yes - The text prompt describing the image you want to generate.
- `last_image`: string No - - The last image for generating the output.
- `resolution`: string No 480p 480p, 720p The resolution of the generated media.
- `aspect_ratio`: string No 1:1 1:1, 4:3, 3:4, 2:3, 16:9, 1:2 The aspect ratio of the generated media.
- `motion`: string No low low, high The motion of the generated media.
- `quality`: number No 1 0.25, 0.5, 1, 2 Use the quality parameter to control image detail and processing time.
- `stylize`: integer No - 0 ~ 1000 Use the stylize parameter to control the artistic style in the image (0-1000).
- `chaos`: integer No - 0 ~ 100 Use the chaos parameter to add variety to your image results (0-100). Higher values produce more unusual and unexpected results.
- `weird`: integer No - 0 ~ 3000 Use the weird parameter to make your images quirky and unconventional (0-3000).
- `seed`: integer No -1 -1 ~ 2147483647 Use the seed parameter for testing and experimentation. Use the same seed and prompt to get similar results.
- `enable_base64_output`: boolean No false - The random seed to use for the generation.

### Minimax Hailuo 02 I2V Pro

- **Model ID:** `minimax/hailuo-02/i2v-pro`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/minimax/hailuo-02/i2v-pro`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/minimax/minimax-hailuo-02-i2v-pro

**Request Parameters**

- `image`: string Yes - The model generates video with the picture passed in as the first frame.Base64 encoded strings in data:image/jpeg; base64,{data} format for incoming images, or URLs accessible via the public network. The uploaded image needs to
- `prompt`: string No - The positive prompt for the generation.
- `end_image`: string No - - The model generates video with the picture passed in as the first frame.Base64 encoded strings in data:image/jpeg; base64,{data} format for incoming images, or URLs accessible via the public network. The uploaded image needs t
- `enable_prompt_expansion`: boolean No true - The model automatically optimizes incoming prompts to enhance output quality. This also activates the safety checker, which ensures content safety by detecting and filtering potential risks.

### Minimax Hailuo 02 I2V Standard

- **Model ID:** `minimax/hailuo-02/i2v-standard`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/minimax/hailuo-02/i2v-standard`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/minimax/minimax-hailuo-02-i2v-standard

**Request Parameters**

- `image`: string Yes - The model generates video with the picture passed in as the first frame.Base64 encoded strings in data:image/jpeg; base64,{data} format for incoming images, or URLs accessible via the public network. The uploaded image needs to
- `prompt`: string No - The positive prompt for the generation.
- `end_image`: string No - - The model generates video with the picture passed in as the last frame.Base64 encoded strings in data:image/jpeg; base64,{data} format for incoming images, or URLs accessible via the public network. The uploaded image needs to
- `duration`: integer No 6 6, 10 The duration of the generated media in seconds.
- `enable_prompt_expansion`: boolean No false - The model automatically optimizes incoming prompts to enhance output quality. This also activates the safety checker, which ensures content safety by detecting and filtering potential risks.

### Minimax Hailuo 02 T2V Pro

- **Model ID:** `minimax/hailuo-02/t2v-pro`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/minimax/hailuo-02/t2v-pro`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/minimax/minimax-hailuo-02-t2v-pro

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `enable_prompt_expansion`: boolean No true - The model automatically optimizes incoming prompts to enhance output quality. This also activates the safety checker, which ensures content safety by detecting and filtering potential risks.

### Minimax Hailuo 02 T2V Standard

- **Model ID:** `minimax/hailuo-02/t2v-standard`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/minimax/hailuo-02/t2v-standard`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/minimax/minimax-hailuo-02-t2v-standard

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `duration`: integer No 6 6, 10 The duration of the generated media in seconds.
- `enable_prompt_expansion`: boolean No true - The model automatically optimizes incoming prompts to enhance output quality. This also activates the safety checker, which ensures content safety by detecting and filtering potential risks.

### Minimax Hailuo 2.3 I2V Pro

- **Model ID:** `minimax/hailuo-2.3/i2v-pro`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/minimax/hailuo-2.3/i2v-pro`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/minimax/minimax-hailuo-2.3-i2v-pro

**Request Parameters**

- `image`: string Yes - The model generates video with the picture passed in as the first frame.Base64 encoded strings in data:image/jpeg; base64,{data} format for incoming images, or URLs accessible via the public network. The uploaded image needs to
- `prompt`: string No - The positive prompt for the generation.
- `enable_prompt_expansion`: boolean No true - The model automatically optimizes incoming prompts to enhance output quality. This also activates the safety checker, which ensures content safety by detecting and filtering potential risks.

### Minimax Hailuo 2.3 I2V Standard

- **Model ID:** `minimax/hailuo-2.3/i2v-standard`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/minimax/hailuo-2.3/i2v-standard`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/minimax/minimax-hailuo-2.3-i2v-standard

**Request Parameters**

- `image`: string Yes - The model generates video with the picture passed in as the first frame.Base64 encoded strings in data:image/jpeg; base64,{data} format for incoming images, or URLs accessible via the public network. The uploaded image needs to
- `prompt`: string No - The positive prompt for the generation.
- `duration`: integer No 6 6, 10 The duration of the generated media in seconds.
- `enable_prompt_expansion`: boolean No false - The model automatically optimizes incoming prompts to enhance output quality. This also activates the safety checker, which ensures content safety by detecting and filtering potential risks.

### Minimax Hailuo 2.3 T2V Pro

- **Model ID:** `minimax/hailuo-2.3/t2v-pro`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/minimax/hailuo-2.3/t2v-pro`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/minimax/minimax-hailuo-2.3-t2v-pro

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `enable_prompt_expansion`: boolean No true - The model automatically optimizes incoming prompts to enhance output quality. This also activates the safety checker, which ensures content safety by detecting and filtering potential risks.

### Minimax Hailuo 2.3 T2V Standard

- **Model ID:** `minimax/hailuo-2.3/t2v-standard`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/minimax/hailuo-2.3/t2v-standard`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/minimax/minimax-hailuo-2.3-t2v-standard

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `duration`: integer No 6 6, 10 The duration of the generated media in seconds.
- `enable_prompt_expansion`: boolean No true - The model automatically optimizes incoming prompts to enhance output quality. This also activates the safety checker, which ensures content safety by detecting and filtering potential risks.

### Minimax Video 01

- **Model ID:** `minimax/video-01`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/minimax/video-01`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/minimax/minimax-video-01

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `image`: string No - The model generates video with the picture passed in as the first frame.Base64 encoded strings in data:image/jpeg; base64,{data} format for incoming images, or URLs accessible via the public network. The uploaded image needs to 
- `enable_prompt_expansion`: boolean No true - The model automatically optimizes incoming prompts to improve build quality.

### Minimax Video 02

- **Model ID:** `minimax/video-02`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/minimax/video-02`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/minimax/minimax-video-02

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `image`: string No - The model generates video with the picture passed in as the first frame.Base64 encoded strings in data:image/jpeg; base64,{data} format for incoming images, or URLs accessible via the public network. The uploaded image needs to 
- `resolution`: string No 768p 768p, 1080p Video resolution.
- `duration`: integer No 6 6 The duration of the generated media in seconds.
- `enable_prompt_expansion`: boolean No false - The model automatically optimizes incoming prompts to enhance output quality. This also activates the safety checker, which ensures content safety by detecting and filtering potential risks.

### Mirelo AI Sfx V1.5 Video To Video

- **Model ID:** `mirelo-ai/sfx-v1.5/video-to-video`
- **Operation:** `video_edit`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/mirelo-ai/sfx-v1.5/video-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/mirelo-ai/mirelo-ai-sfx-v1.5-video-to-video

**Request Parameters**

- `video`: string Yes - The video for generating the output.
- `prompt`: string No - Text prompt to guide sound effect generation
- `num_samples`: integer No 2 2 ~ 4 Number of sound effects to generate
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Mirelo AI Sfx V1 Video To Audio

- **Model ID:** `mirelo-ai/sfx-v1/video-to-audio`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/mirelo-ai/sfx-v1/video-to-audio`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/mirelo-ai/mirelo-ai-sfx-v1-video-to-audio

**Request Parameters**

- `video`: string Yes - The video for generating the output.
- `prompt`: string No - Text prompt to guide sound effect generation
- `num_samples`: integer No 2 1 ~ 8 Number of sound effects to generate
- `duration`: number No 5 2 ~ 10 Number of sound effects to generate
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Nvidia Nemotron 3 Nano Omni Video

- **Model ID:** `nvidia/nemotron-3-nano-omni/video`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/nvidia/nemotron-3-nano-omni/video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/nvidia/nvidia-nemotron-3-nano-omni-video

**Request Parameters**

- `prompt`: string Yes - Text prompt to send to the model. English only.
- `video_url`: string Yes - - URL of the video to reason about.
- `system_prompt`: string No - - Optional system prompt to steer the model.
- `reasoning_mode`: string No no_think no_think, think Whether the model should emit an explicit reasoning trace.
- `max_tokens`: integer No 1024 - Maximum number of tokens to generate.
- `temperature`: number No 0.7 - Sampling temperature. Lower values are more deterministic.
- `top_p`: number No 0.95 0 ~ 1 Nucleus sampling probability mass.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Openai Sora 2 Pro Image To Video

- **Model ID:** `openai/sora-2-pro/image-to-video`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/openai/sora-2-pro/image-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/openai/openai-sora-2-pro-image-to-video

**Request Parameters**

- `image`: string Yes - The image for generating the output.
- `prompt`: string Yes - The positive prompt for the generation.
- `resolution`: string No 720p 720p, 1080p The resolution of the generated video.
- `duration`: integer No 4 4, 8, 12, 16, 20 The duration of the generated video in seconds.

### Openai Sora 2 Pro Text To Video

- **Model ID:** `openai/sora-2-pro/text-to-video`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/openai/sora-2-pro/text-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/openai/openai-sora-2-pro-text-to-video

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 1280*720 720*1280, 1280*720, 1024*1792, 1792*1024, 1920*1080, 1080*1920 The size of the generated media in pixels (width*height).
- `duration`: integer No 4 4, 8, 12, 16, 20 The duration of the generated video in seconds.
- `characters`: array No - - Element reference list. To get available elements and their IDs, visit: https://wavespeed.ai/models/openai/sora-2/characters

### Openai Sora 2 Image To Video

- **Model ID:** `openai/sora-2/image-to-video`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/openai/sora-2/image-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/openai/openai-sora-2-image-to-video

**Request Parameters**

- `image`: string Yes - The image for generating the output.
- `prompt`: string Yes - The positive prompt for the generation.
- `duration`: integer No 4 4, 8, 12, 16, 20 The duration of the generated video in seconds.

### Openai Sora 2 Image To Video Pro

- **Model ID:** `openai/sora-2/image-to-video-pro`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/openai/sora-2/image-to-video-pro`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/openai/openai-sora-2-image-to-video-pro

**Request Parameters**

- `image`: string Yes - The image for generating the output.
- `prompt`: string Yes - The positive prompt for the generation.
- `resolution`: string No 720p 720p, 1080p The resolution of the generated video.
- `duration`: integer No 4 4, 8, 12, 16, 20 The duration of the generated video in seconds.

### Openai Sora 2 Text To Video

- **Model ID:** `openai/sora-2/text-to-video`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/openai/sora-2/text-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/openai/openai-sora-2-text-to-video

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 720*1280 720*1280, 1280*720 The size of the generated media in pixels (width*height).
- `duration`: integer No 4 4, 8, 12, 16, 20 The duration of the generated video in seconds.
- `characters`: array No - - Element reference list. To get available elements and their IDs, visit: https://wavespeed.ai/models/openai/sora-2/characters

### Openai Sora 2 Text To Video Pro

- **Model ID:** `openai/sora-2/text-to-video-pro`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/openai/sora-2/text-to-video-pro`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/openai/openai-sora-2-text-to-video-pro

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 720*1280 720*1280, 1280*720, 1024*1792, 1792*1024, 1920*1080, 1080*1920 The size of the generated media in pixels (width*height).
- `duration`: integer No 4 4, 8, 12, 16, 20 The duration of the generated video in seconds.
- `characters`: array No - - Element reference list. To get available elements and their IDs, visit: https://wavespeed.ai/models/openai/sora-2/characters

### Pika V2.0 Turbo I2V

- **Model ID:** `pika/v2.0-turbo-i2v`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/pika/v2.0-turbo-i2v`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/pika/pika-v2.0-turbo-i2v

**Request Parameters**

- `image`: string Yes - The image for generating the output.
- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 1280*720 1280*720, 720*1280 The size of the generated media in pixels (width*height).
- `duration`: integer No 5 5, 10 The duration of the generated media in seconds.

### Pika V2.0 Turbo T2V

- **Model ID:** `pika/v2.0-turbo-t2v`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/pika/v2.0-turbo-t2v`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/pika/pika-v2.0-turbo-t2v

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 1280*720 1280*720, 720*1280 The size of the generated media in pixels (width*height).
- `duration`: integer No 5 5, 10 The duration of the generated media in seconds.

### Pika V2.1 I2V

- **Model ID:** `pika/v2.1-i2v`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/pika/v2.1-i2v`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/pika/pika-v2.1-i2v

**Request Parameters**

- `image`: string Yes - The image for generating the output.
- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 1280*720 1280*720, 720*1280 The size of the generated media in pixels (width*height).
- `duration`: integer No 5 5, 10 The duration of the generated media in seconds.

### Pika V2.1 T2V

- **Model ID:** `pika/v2.1-t2v`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/pika/v2.1-t2v`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/pika/pika-v2.1-t2v

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 1280*720 1280*720, 720*1280 The size of the generated media in pixels (width*height).
- `duration`: integer No 5 5, 10 The duration of the generated media in seconds.

### Pika V2.2 I2V

- **Model ID:** `pika/v2.2-i2v`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/pika/v2.2-i2v`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/pika/pika-v2.2-i2v

**Request Parameters**

- `image`: string Yes - The image for generating the output.
- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 1280*720 1280*720, 720*1280 The size of the generated media in pixels (width*height).
- `duration`: integer No 5 5, 10 The duration of the generated media in seconds.

### Pika V2.2 T2V

- **Model ID:** `pika/v2.2-t2v`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/pika/v2.2-t2v`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/pika/pika-v2.2-t2v

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 1280*720 1280*720, 720*1280 The size of the generated media in pixels (width*height).
- `duration`: integer No 5 5, 10 The duration of the generated media in seconds.

### Pixverse Pixverse C1 Image To Video

- **Model ID:** `pixverse/pixverse-c1/image-to-video`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/pixverse/pixverse-c1/image-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/pixverse/pixverse-pixverse-c1-image-to-video

**Request Parameters**

- `image`: string Yes - URL of the first frame image to animate.
- `prompt`: string Yes - Text prompt describing the desired animation.
- `resolution`: string No 720p 360p, 540p, 720p, 1080p Resolution quality of the generated video.
- `duration`: integer No 5 1 ~ 15 Duration of the video in seconds (1-15).
- `generate_audio_switch`: boolean No false - Whether to generate native audio for the video.

### Pixverse Pixverse C1 Reference To Video

- **Model ID:** `pixverse/pixverse-c1/reference-to-video`
- **Operation:** `reference_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/pixverse/pixverse-c1/reference-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/pixverse/pixverse-pixverse-c1-reference-to-video

**Request Parameters**

- `prompt`: string Yes - Text description of the desired video.
- `images`: array Yes [] 1 ~ 7 items This is a controlnet that controls the maximum size of the generated model.
- `aspect_ratio`: string No 16:9 16:9, 4:3, 1:1, 3:4, 9:16, 2:3, 3:2, 21:9 The aspect ratio of the generated video
- `resolution`: string No 720p 360p, 540p, 720p, 1080p The resolution of the generated video
- `duration`: integer No 5 1 ~ 15 The duration of the generated video in seconds. v6 supports values from 1 to 15 seconds
- `generate_audio_switch`: boolean No false - Enable audio generation for the video.
- `seed`: integer No - -1 ~ 2147483647 Random seed for generation.

### Pixverse Pixverse C1 Text To Video

- **Model ID:** `pixverse/pixverse-c1/text-to-video`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/pixverse/pixverse-c1/text-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/pixverse/pixverse-pixverse-c1-text-to-video

**Request Parameters**

- `prompt`: string Yes - Text prompt describing the video to generate.
- `aspect_ratio`: string No 16:9 16:9, 4:3, 1:1, 3:4, 9:16, 2:3, 3:2, 21:9 Aspect ratio of the generated video.
- `resolution`: string No 720p 360p, 540p, 720p, 1080p Resolution quality of the generated video.
- `duration`: integer No 5 1 ~ 15 Duration of the video in seconds (1-15).
- `generate_audio_switch`: boolean No false - Whether to generate native audio for the video.

### Pixverse Pixverse C1 Transition

- **Model ID:** `pixverse/pixverse-c1/transition`
- **Operation:** `frame_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/pixverse/pixverse-c1/transition`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/pixverse/pixverse-pixverse-c1-transition

**Request Parameters**

- `prompt`: string Yes - Text prompt describing the transition between the two frames.
- `image`: string Yes - URL of the image to use as the first frame.
- `end_image`: string Yes - - URL of the image to use as the last frame.
- `aspect_ratio`: string No 16:9 16:9, 4:3, 1:1, 3:4, 9:16, 2:3, 3:2, 21:9 Aspect ratio of the generated video.
- `resolution`: string No 720p 360p, 540p, 720p, 1080p Resolution quality of the generated video.
- `duration`: integer No 5 1 ~ 15 Duration of the video in seconds (1-15).
- `generate_audio_switch`: boolean No false - Whether to generate native audio for the video.

### Pixverse Pixverse V4.5 I2V

- **Model ID:** `pixverse/pixverse-v4.5-i2v`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/pixverse/pixverse-v4.5-i2v`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/pixverse/pixverse-pixverse-v4.5-i2v

**Request Parameters**

- `image`: string Yes - First frame of the video; Supported image formats include.jpg/.jpeg/.png; The image file size cannot exceed 10MB, and the image resolution should not be less than 300*300px, and the aspect ratio of the image should be between 1
- `prompt`: string Yes - The positive prompt for the generation.
- `resolution`: string No 540p 360p, 540p, 720p, 1080p Video quality (360p/540p/720p/1080p).
- `duration`: integer No 5 5, 8 Video duration in seconds. 1080p only supports 5 seconds.
- `negative_prompt`: string No - The negative prompt for the generation.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation.

### Pixverse Pixverse V4.5 I2V Fast

- **Model ID:** `pixverse/pixverse-v4.5-i2v-fast`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/pixverse/pixverse-v4.5-i2v-fast`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/pixverse/pixverse-pixverse-v4.5-i2v-fast

**Request Parameters**

- `image`: string Yes - First frame of the video; Supported image formats include.jpg/.jpeg/.png; The image file size cannot exceed 10MB, and the image resolution should not be less than 300*300px, and the aspect ratio of the image should be between 1
- `prompt`: string Yes - The positive prompt for the generation.
- `resolution`: string No 540p 360p, 540p, 720p Video quality (360p/540p/720p/1080p).
- `duration`: integer No 5 5 Video duration in seconds.
- `negative_prompt`: string No - The negative prompt for the generation.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation.

### Pixverse Pixverse V4.5 T2V

- **Model ID:** `pixverse/pixverse-v4.5-t2v`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/pixverse/pixverse-v4.5-t2v`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/pixverse/pixverse-pixverse-v4.5-t2v

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `aspect_ratio`: string Yes 16:9 16:9, 4:3, 1:1, 3:4, 9:16 The aspect ratio of the generated media.
- `resolution`: string Yes 540p 360p, 540p, 720p, 1080p Video quality (360p/540p/720p/1080p).
- `duration`: integer No 5 5, 8 Video duration in seconds. 1080p only supports 5 seconds.
- `negative_prompt`: string No - The negative prompt for the generation.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation.

### Pixverse Pixverse V4.5 T2V Fast

- **Model ID:** `pixverse/pixverse-v4.5-t2v-fast`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/pixverse/pixverse-v4.5-t2v-fast`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/pixverse/pixverse-pixverse-v4.5-t2v-fast

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `aspect_ratio`: string Yes 16:9 16:9, 4:3, 1:1, 3:4, 9:16 The aspect ratio of the generated media.
- `resolution`: string Yes 540p 360p, 540p, 720p Video quality (360p/540p/720p/1080p).
- `duration`: integer No 5 5 Video duration in seconds.
- `negative_prompt`: string No - The negative prompt for the generation.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation.

### Pixverse Pixverse V5 I2V

- **Model ID:** `pixverse/pixverse-v5-i2v`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/pixverse/pixverse-v5-i2v`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/pixverse/pixverse-pixverse-v5-i2v

**Request Parameters**

- `image`: string Yes - First frame of the video; Supported image formats include.jpg/.jpeg/.png; The image file size cannot exceed 10MB, and the image resolution should not be less than 300*300px, and the aspect ratio of the image should be between 1
- `prompt`: string Yes - The positive prompt for the generation.
- `resolution`: string No 540p 360p, 540p, 720p, 1080p Video quality (360p/540p/720p/1080p).
- `duration`: integer No 5 5, 8 Video duration in seconds.
- `sound_effect_switch`: boolean No false - Set to true if you want to enable this feature.
- `negative_prompt`: string No - The negative prompt for the generation.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation.

### Pixverse Pixverse V5 T2V

- **Model ID:** `pixverse/pixverse-v5-t2v`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/pixverse/pixverse-v5-t2v`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/pixverse/pixverse-pixverse-v5-t2v

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `resolution`: string No 540p 360p, 540p, 720p, 1080p Video quality (360p/540p/720p/1080p).
- `aspect_ratio`: string No 16:9 16:9, 4:3, 1:1, 3:4, 9:16 The aspect ratio of the generated media.
- `duration`: integer No 5 5, 8 Video duration in seconds.
- `negative_prompt`: string No - The negative prompt for the generation.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation.

### Pixverse Pixverse V5 Transition

- **Model ID:** `pixverse/pixverse-v5-transition`
- **Operation:** `frame_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/pixverse/pixverse-v5-transition`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/pixverse/pixverse-pixverse-v5-transition

**Request Parameters**

- `image`: string Yes - The model generates video with the picture passed in as the first frame.Base64 encoded strings in data:image/jpeg; base64,{data} format for incoming images, or URLs accessible via the public network. The uploaded image needs to
- `prompt`: string Yes - The positive prompt for the generation.
- `end_image`: string Yes - - The model generates video with the picture passed in as the last frame.Base64 encoded strings in data:image/jpeg; base64,{data} format for incoming images, or URLs accessible via the public network. The uploaded image needs t
- `aspect_ratio`: string No 16:9 16:9, 1:1, 4:3, 3:4, 9:16 The aspect ratio of the generated media.
- `duration`: integer No 5 5, 8 Video duration in seconds.
- `resolution`: string No 540p 360p, 540p, 720p, 1080p Video quality (360p/540p/720p/1080p).
- `negative_prompt`: string No - The negative prompt for the generation.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation.

### Pixverse Pixverse V5.5 Transition

- **Model ID:** `pixverse/pixverse-v5.5-transition`
- **Operation:** `frame_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/pixverse/pixverse-v5.5-transition`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/pixverse/pixverse-pixverse-v5.5-transition

**Request Parameters**

- `image`: string Yes - The model generates video with the picture passed in as the first frame.Base64 encoded strings in data:image/jpeg; base64,{data} format for incoming images, or URLs accessible via the public network. The uploaded image needs to
- `prompt`: string Yes - The positive prompt for the generation.
- `end_image`: string Yes - - The model generates video with the picture passed in as the last frame.Base64 encoded strings in data:image/jpeg; base64,{data} format for incoming images, or URLs accessible via the public network. The uploaded image needs t
- `aspect_ratio`: string No 16:9 16:9, 1:1, 4:3, 3:4, 9:16 The aspect ratio of the generated media.
- `duration`: integer No 5 5, 8, 10 Video duration in seconds.
- `resolution`: string No 540p 360p, 540p, 720p, 1080p Video quality (360p/540p/720p/1080p).
- `thinking_type`: string No auto enabled, disabled, auto Prompt reasoning enhancement. Controls whether the system should enhance your prompt with internal reasoning and optimization. "enabled" : Turn on system-level optimization. "disabled" : Turn off syste
- `negative_prompt`: string No - The negative prompt for the generation.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation.

### Pixverse Pixverse V5.5 Image To Video

- **Model ID:** `pixverse/pixverse-v5.5/image-to-video`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/pixverse/pixverse-v5.5/image-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/pixverse/pixverse-pixverse-v5.5-image-to-video

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `image`: string No - First frame of the video; Supported image formats include.jpg/.jpeg/.png; The image file size cannot exceed 10MB, and the image resolution should not be less than 300*300px, and the aspect ratio of the image should be between 1:
- `resolution`: string No 540p 360p, 540p, 720p, 1080p The resolution of the generated media.
- `duration`: integer No 5 5, 8, 10 The duration of the generated media.
- `generate_audio_switch`: boolean No false - Enable audio generation for the video.
- `generate_multi_clip_switch`: boolean No false - Enable multi-clip generation with dynamic camera changes.
- `thinking_type`: string No auto enabled, disabled, auto Prompt reasoning enhancement.
- `negative_prompt`: string No - The negative prompt for the generation.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation.

### Pixverse Pixverse V5.5 Text To Video

- **Model ID:** `pixverse/pixverse-v5.5/text-to-video`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/pixverse/pixverse-v5.5/text-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/pixverse/pixverse-pixverse-v5.5-text-to-video

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `resolution`: string No 540p 360p, 540p, 720p, 1080p Video quality
- `duration`: integer No 5 5, 8, 10 Video duration in seconds.
- `resolution_ratio`: string No 1:1 16:9, 4:3, 1:1, 3:4, 9:16 The aspect ratio of the generated video
- `generate_audio_switch`: boolean No false - Enable audio generation for the video.
- `generate_multi_clip_switch`: boolean No false - Enable multi-clip generation with dynamic camera changes.
- `thinking_type`: string No auto enabled, disabled, auto Controls whether the system should enhance your prompt with internal reasoning and optimization.
- `negative_prompt`: string No - The negative prompt for the generation.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation.

### Pixverse Pixverse V5.6 Image To Video

- **Model ID:** `pixverse/pixverse-v5.6/image-to-video`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/pixverse/pixverse-v5.6/image-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/pixverse/pixverse-pixverse-v5.6-image-to-video

**Request Parameters**

- `image`: string Yes - First frame of the video; Supported image formats include.jpg/.jpeg/.png; The image file size cannot exceed 10MB, and the image resolution should not be less than 300*300px, and the aspect ratio of the image should be between 1
- `prompt`: string Yes - The positive prompt for the generation.
- `resolution`: string No 540p 360p, 540p, 720p, 1080p The resolution of the generated media.
- `duration`: integer No 5 5, 8, 10 The duration of the generated media.
- `generate_audio_switch`: boolean No false - Enable audio generation for the video.
- `thinking_type`: string No auto enabled, disabled, auto Prompt reasoning enhancement.
- `negative_prompt`: string No - The negative prompt for the generation.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation.

### Pixverse Pixverse V5.6 Text To Video

- **Model ID:** `pixverse/pixverse-v5.6/text-to-video`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/pixverse/pixverse-v5.6/text-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/pixverse/pixverse-pixverse-v5.6-text-to-video

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `resolution`: string No 540p 360p, 540p, 720p, 1080p Video quality
- `duration`: integer No 5 5, 8, 10 Video duration in seconds.
- `resolution_ratio`: string No 1:1 16:9, 4:3, 1:1, 3:4, 9:16 The aspect ratio of the generated video
- `generate_audio_switch`: boolean No false - Enable audio generation for the video.
- `thinking_type`: string No auto enabled, disabled, auto Controls whether the system should enhance your prompt with internal reasoning and optimization.
- `negative_prompt`: string No - The negative prompt for the generation.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation.

### Pixverse Pixverse V6 Image To Video

- **Model ID:** `pixverse/pixverse-v6/image-to-video`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/pixverse/pixverse-v6/image-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/pixverse/pixverse-pixverse-v6-image-to-video

**Request Parameters**

- `image`: string Yes - The URL of the input image for video generation.
- `prompt`: string Yes - Text prompt describing the video to generate.
- `resolution`: string No 720p 360p, 540p, 720p, 1080p Resolution quality of the generated video.
- `duration`: integer No 5 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15 Duration of the video in seconds (1-15).
- `generate_audio_switch`: boolean No false - Whether to generate audio for the video.
- `thinking_type`: string No auto enabled, disabled, auto Prompt reasoning mode. Enabled: optimize prompt, Disabled: use as-is, Auto: let system decide.

### Pixverse Pixverse V6 Text To Video

- **Model ID:** `pixverse/pixverse-v6/text-to-video`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/pixverse/pixverse-v6/text-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/pixverse/pixverse-pixverse-v6-text-to-video

**Request Parameters**

- `prompt`: string Yes - Text prompt describing the video to generate.
- `aspect_ratio`: string No 16:9 16:9, 4:3, 1:1, 3:4, 9:16, 2:3, 3:2, 21:9 Aspect ratio of the generated video.
- `resolution`: string No 720p 360p, 540p, 720p, 1080p Resolution quality of the generated video.
- `duration`: integer No 5 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15 Duration of the video in seconds (1-15).
- `generate_audio_switch`: boolean No false - Whether to generate audio for the video.
- `thinking_type`: string No auto enabled, disabled, auto Prompt reasoning mode. Enabled: optimize prompt, Disabled: use as-is, Auto: let system decide.

### Pixverse Pixverse V6 Transition

- **Model ID:** `pixverse/pixverse-v6/transition`
- **Operation:** `frame_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/pixverse/pixverse-v6/transition`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/pixverse/pixverse-pixverse-v6-transition

**Request Parameters**

- `prompt`: string Yes - Text description of the desired video.
- `image`: string Yes - URL of the image to use as the first frame
- `end_image`: string No - - URL of the image to use as the last frame
- `aspect_ratio`: string No 16:9 16:9, 4:3, 1:1, 3:4, 9:16, 2:3, 3:2, 21:9 The aspect ratio of the generated video
- `resolution`: string No 720p 360p, 540p, 720p, 1080p The resolution of the generated video
- `duration`: integer No 5 1 ~ 15 The duration of the generated video in seconds. v6 supports values from 1 to 15 seconds
- `thinking_type`: boolean No false - Prompt optimization mode
- `generate_audio_switch`: boolean No false - Enable audio generation for the video.
- `generate_multi_clip_switch`: boolean No false - Enable multi-clip generation with dynamic camera changes
- `style`: string No - anime, 3d_animation, clay, comic, cyberpunk The style of the extended video
- `negative_prompt`: string No - The negative prompt for the generation.
- `seed`: integer No - -1 ~ 2147483647 Random seed for generation.

### Video Effects Balloon Flyaway

- **Model ID:** `video-effects/balloon-flyaway`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/video-effects/balloon-flyaway`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/video-effects/video-effects-balloon-flyaway

**Request Parameters**

- `image`: string Yes - An image to be used as the start frame of the generated video. Requirements: 1. Only accepts 1 image; 2. Supports single-subject, dual-subject, and multi-subject in realistic style characters and animals; 3. Best results achiev

### Video Effects Blow Kiss

- **Model ID:** `video-effects/blow-kiss`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/video-effects/blow-kiss`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/video-effects/video-effects-blow-kiss

**Request Parameters**

- `image`: string Yes - An image to be used as the start frame of the generated video. Requirements: 1. Only accepts 1 image; 2. Supports single or double subjects; 3. Best results when subjects are not holding any props; 4. Images can be provided via

### Video Effects Blueprint Supreme

- **Model ID:** `video-effects/blueprint-supreme`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/video-effects/blueprint-supreme`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/video-effects/video-effects-blueprint-supreme

**Request Parameters**

- `image`: string Yes - An image to be used as the start frame of the generated video. Requirements: 1. Only accepts 1 image; 2. Only supports single-person input; 3. Only images with an aspect ratio between 1:2 and 1:1.2 are supported; 4. Full-body, 
- `bgm`: boolean Yes true - Enable or disable background music. Default is true.

### Video Effects Body Shake

- **Model ID:** `video-effects/body-shake`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/video-effects/body-shake`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/video-effects/video-effects-body-shake

**Request Parameters**

- `image`: string Yes - An image to be used as the start frame of the generated video. Requirements: 1. Only accepts 1 image; 2. Supports single-person photos in realistic or anime style; 3. Best results when the person is fully visible from above the

### Video Effects Break Glass

- **Model ID:** `video-effects/break-glass`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/video-effects/break-glass`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/video-effects/video-effects-break-glass

**Request Parameters**

- `image`: string Yes - An image to be used as the start frame of the generated video. Requirements: 1. Only accepts 1 image; 2. Supports single or two subjects, including animals; 3. Best for front-facing upper or full body images; 4. Images can be p

### Video Effects Cap Walk

- **Model ID:** `video-effects/cap-walk`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/video-effects/cap-walk`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/video-effects/video-effects-cap-walk

**Request Parameters**

- `image`: string Yes - An image to be used as the start frame of the generated video. Requirements: 1. Only accepts 1 image; 2. Only supports single-person photos; 3. Best for half-body or full-body photos; 4. Images can be provided via URLs or Base6

### Video Effects Captain America

- **Model ID:** `video-effects/captain-america`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/video-effects/captain-america`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/video-effects/video-effects-captain-america

**Request Parameters**

- `image`: string Yes - An image to be used as the start frame of the generated video. Requirements: 1. Only accepts 1 image; 2. Only supports single-person photos; 3. Best for half-body or full-body photos; 4. Images can be provided via URLs or Base6

### Video Effects Carry Me

- **Model ID:** `video-effects/carry-me`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/video-effects/carry-me`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/video-effects/video-effects-carry-me

**Request Parameters**

- `image`: string Yes - An image to be used as the start frame of the generated video. Requirements: 1. Only accepts 1 image; 2. Only supports two-person photos; 3. Better results when two people stand side by side at a moderate distance, showing from

### Video Effects Cartoon Doll

- **Model ID:** `video-effects/cartoon-doll`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/video-effects/cartoon-doll`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/video-effects/video-effects-cartoon-doll

**Request Parameters**

- `image`: string Yes - An image to be used as the start frame of the generated video. Requirements: 1. Only accepts 1 image; 2. Only supports realistic-style single-person photos; 3. Best results when the person is fully visible (especially children)

### Video Effects Child Memory

- **Model ID:** `video-effects/child-memory`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/video-effects/child-memory`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/video-effects/video-effects-child-memory

**Request Parameters**

- `image`: string Yes - An image to be used as the start frame of the generated video. Requirements: 1. Only accepts 1 image; 2. Supports single-person photos; 3. Best for front-facing subjects with clear upper body visibility and blank space on both 

### Video Effects Claysho

- **Model ID:** `video-effects/claysho`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/video-effects/claysho`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/video-effects/video-effects-claysho

**Request Parameters**

- `image`: string Yes - An image to be used as the start frame of the generated video. Requirements: 1. Only accepts 1 image; 2. Supports adults, children, animals, two-person or three-person family scenarios; 3. Best for front-facing upper or full bo

### Video Effects Couple Arrival

- **Model ID:** `video-effects/couple-arrival`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/video-effects/couple-arrival`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/video-effects/video-effects-couple-arrival

**Request Parameters**

- `image`: string Yes - An image to be used as the start frame of the generated video. Requirements: 1. Only accepts 1 image; 2. Only supports single person; 3. Half-body front-facing photo for better results; 4. Images can be provided via URLs or Bas

### Video Effects Couple Hugging

- **Model ID:** `video-effects/couple-hugging`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/video-effects/couple-hugging`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/video-effects/video-effects-couple-hugging

**Request Parameters**

- `image`: string Yes - An image to be used as the start frame of the generated video. Requirements: 1. Only accepts 1 image; 2. Only single person supported; 3. Half-body front-facing photo of two subjects for better results; 4. Images can be provide

### Video Effects Dreamy Wedding

- **Model ID:** `video-effects/dreamy-wedding`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/video-effects/dreamy-wedding`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/video-effects/video-effects-dreamy-wedding

**Request Parameters**

- `image`: string Yes - An image to be used as the start frame of the generated video. Requirements: 1. Only accepts 1 image; 2. Supports two-person photos only; 3. Best for front-facing subjects with clear face visibility above neck and no hair acces

### Video Effects Dust Me Away

- **Model ID:** `video-effects/dust-me-away`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/video-effects/dust-me-away`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/video-effects/video-effects-dust-me-away

**Request Parameters**

- `image`: string Yes - First frame of the video; Supported image formats include.jpg/.jpeg/.png; The image file size cannot exceed 10MB, and the image resolution should not be less than 300*300px, and the aspect ratio of the image should be between 1
- `resolution`: string No 540p 360p, 540p, 720p Video quality (360p/540p/720p/1080p).

### Video Effects Exotic Princess

- **Model ID:** `video-effects/exotic-princess`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/video-effects/exotic-princess`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/video-effects/video-effects-exotic-princess

**Request Parameters**

- `image`: string Yes - An image to be used as the start frame of the generated video. Requirements: 1. Only accepts 1 image; 2. Only single person supported; 3. Female facial features photo (without hairstyle or clothing) for best results; 4. Images 

### Video Effects Fairy Me

- **Model ID:** `video-effects/fairy-me`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/video-effects/fairy-me`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/video-effects/video-effects-fairy-me

**Request Parameters**

- `image`: string Yes - An image to be used as the start frame of the generated video. Requirements: 1. Only accepts 1 image; 2. Only supports single-person female photos; 3. Better results when the subject is standing, holding no props, and the full 

### Video Effects Fashion Stride

- **Model ID:** `video-effects/fashion-stride`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/video-effects/fashion-stride`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/video-effects/video-effects-fashion-stride

**Request Parameters**

- `image`: string Yes - An image to be used as the start frame of the generated video. Requirements: 1. Only accepts 1 image; 2. Only supports single person or single pet; 3. Half-body or full-body human with full-body pet for better results; 4. Image

### Video Effects Fishermen

- **Model ID:** `video-effects/fishermen`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/video-effects/fishermen`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/video-effects/video-effects-fishermen

**Request Parameters**

- `image`: string Yes - An image to be used as the start frame of the generated video. Requirements: 1. Only accepts 1 image; 2. Supports single-person photos, two-person group photos, and pet photos; 3. Best results when the main subject's upper body

### Video Effects Flame Carpet

- **Model ID:** `video-effects/flame-carpet`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/video-effects/flame-carpet`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/video-effects/video-effects-flame-carpet

**Request Parameters**

- `image`: string Yes - An image to be used as the start frame of the generated video. Requirements: 1. Only accepts 1 image; 2. Only single person and single pet supported; 3. Half-body or full-body human with full-body pet for better results; 4. Ima

### Video Effects Flower Receive

- **Model ID:** `video-effects/flower-receive`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/video-effects/flower-receive`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/video-effects/video-effects-flower-receive

**Request Parameters**

- `image`: string Yes - An image to be used as the start frame of the generated video. Requirements: 1. Only accepts 1 image; 2. Supports single-person, two-person, and group photos; 3. Best results are achieved when the main subject's upper body is c

### Video Effects Fluffy Plunge

- **Model ID:** `video-effects/fluffy-plunge`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/video-effects/fluffy-plunge`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/video-effects/video-effects-fluffy-plunge

**Request Parameters**

- `image`: string Yes - An image to be used as the start frame of the generated video. Requirements: 1. Only accepts 1 image; 2. Only a single pet is supported; 3. Only single-subject (pet) images are supported, multiple subjects may affect video qual

### Video Effects Flying

- **Model ID:** `video-effects/flying`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/video-effects/flying`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/video-effects/video-effects-flying

**Request Parameters**

- `image`: string Yes - An image to be used as the start frame of the generated video. Requirements: 1. Only accepts 1 image; 2. Only supports realistic-style single-person photos; 3. Best results achieved with full-body shots and scenic backgrounds; 

### Video Effects French Kiss

- **Model ID:** `video-effects/french-kiss`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/video-effects/french-kiss`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/video-effects/video-effects-french-kiss

**Request Parameters**

- `image`: string Yes - An image to be used as the start frame of the generated video. For fields that accept images: Only accepts 1 image; Images Assets can be provided via URLs or Base64 encode; You must use one of the following codecs: PNG, JPEG, J

### Video Effects Gender Swap

- **Model ID:** `video-effects/gender-swap`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/video-effects/gender-swap`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/video-effects/video-effects-gender-swap

**Request Parameters**

- `image`: string Yes - An image to be used as the start frame of the generated video. Requirements: 1. Only accepts 1 image; 2. Supports solo male or female portraits only; 3. Optimal results with half-body or full-body portraits; 4. Images can be pr

### Video Effects Ghibli

- **Model ID:** `video-effects/ghibli`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/video-effects/ghibli`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/video-effects/video-effects-ghibli

**Request Parameters**

- `image`: string Yes - An image to be used as the start frame of the generated video. Requirements: 1. Only accepts 1 image; 2. Supports single person, single object, two-person subjects, or pure landscape photos; 3. Overall performance is stable and

### Video Effects Golden Epoch

- **Model ID:** `video-effects/golden-epoch`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/video-effects/golden-epoch`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/video-effects/video-effects-golden-epoch

**Request Parameters**

- `image`: string Yes - An image to be used as the start frame of the generated video. Requirements: 1. Only accepts 1 image; 2. Only supports single person; 3. Best for facial features photo (without hairstyle or clothing); 4. Images can be provided 

### Video Effects Hair Swap

- **Model ID:** `video-effects/hair-swap`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/video-effects/hair-swap`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/video-effects/video-effects-hair-swap

**Request Parameters**

- `image`: string Yes - An image to be used as the start frame of the generated video. Requirements: 1. Only accepts 1 image; 2. Single person/animal or dual person/animal supported; 3. Single-person front-facing upper-body pose for best results; 4. I

### Video Effects Hugging

- **Model ID:** `video-effects/hugging`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/video-effects/hugging`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/video-effects/video-effects-hugging

**Request Parameters**

- `image`: string Yes - An image to be used as the start frame of the generated video. Requirements: 1. Only accepts 1 image; 2. Only supports dual-person collage/group photo, or person-pet collage/group photo; 3. Over-half-body exposure with no props

### Video Effects Hulk

- **Model ID:** `video-effects/hulk`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/video-effects/hulk`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/video-effects/video-effects-hulk

**Request Parameters**

- `image`: string Yes - An image to be used as the start frame of the generated video. Requirements: 1. Only accepts 1 image; 2. Only supports single-person photos; 3. Best for half-body or full-body photos; 4. Images can be provided via URLs or Base6

### Video Effects Hulk Dive

- **Model ID:** `video-effects/hulk-dive`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/video-effects/hulk-dive`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/video-effects/video-effects-hulk-dive

**Request Parameters**

- `image`: string Yes - An image to be used as the start frame of the generated video. Requirements: 1. Only accepts 1 image; 2. Only single person supported; 3. Half-body or full-body photo for better results; 4. Images can be provided via URLs or Ba

### Video Effects Jiggle Up

- **Model ID:** `video-effects/jiggle-up`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/video-effects/jiggle-up`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/video-effects/video-effects-jiggle-up

**Request Parameters**

- `image`: string Yes - An image to be used as the start frame of the generated video. Requirements: 1. Only accepts 1 image; 2. Only supports realistic or anime-style single female photos; 3. Best results when the subject is shown from the front and 

### Video Effects Ladudu Me Random

- **Model ID:** `video-effects/ladudu-me-random`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/video-effects/ladudu-me-random`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/video-effects/video-effects-ladudu-me-random

**Request Parameters**

- `image`: string Yes -

### Video Effects Live Memory

- **Model ID:** `video-effects/live-memory`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/video-effects/live-memory`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/video-effects/video-effects-live-memory

**Request Parameters**

- `image`: string Yes - An image to be used as the start frame of the generated video. Requirements: 1. Only accepts 1 image; 2. Supports single or multiple product images; 3. Best for front-facing human figures with simple poses and clear photos; 4. 

### Video Effects Love Drop

- **Model ID:** `video-effects/love-drop`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/video-effects/love-drop`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/video-effects/video-effects-love-drop

**Request Parameters**

- `image`: string Yes - An image to be used as the start frame of the generated video. Requirements: 1. Only accepts 1 image; 2. Supports single-person photos, or photos of one person with one pet; 3. Best results are achieved when the main subject's 

### Video Effects Love Story

- **Model ID:** `video-effects/love-story`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/video-effects/love-story`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/video-effects/video-effects-love-story

**Request Parameters**

- `image`: string Yes - An image to be used as the start frame of the generated video. Requirements: 1. Only accepts 1 image; 2. Only supports two-person photos; 3. Best for full-body photos with visible feet and thighs; 4. Only supports 9:16 aspect r

### Video Effects Manga Meme

- **Model ID:** `video-effects/manga-meme`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/video-effects/manga-meme`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/video-effects/video-effects-manga-meme

**Request Parameters**

- `image`: string Yes - An image to be used as the start frame of the generated video. Requirements: 1. Only accepts 1 image; 2. Supports single person, pet, two persons, person and pet combination, three-person family or multi-person scenarios; 3. Be

### Video Effects Melt

- **Model ID:** `video-effects/melt`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/video-effects/melt`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/video-effects/video-effects-melt

**Request Parameters**

- `image`: string Yes - An image to be used as the start frame of the generated video. Requirements: 1. Only accepts 1 image; 2. Supports single person, dual-person, and multi-person photos; 3. Front-facing full-body or upper-body pose for best result

### Video Effects Minecraft

- **Model ID:** `video-effects/minecraft`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/video-effects/minecraft`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/video-effects/video-effects-minecraft

**Request Parameters**

- `image`: string Yes - An image to be used as the start frame of the generated video. Requirements: 1. Only accepts 1 image; 2. Supports single, dual, or multiple realistic-style human or animal subjects; 3. Best when there is a single subject (human

### Video Effects Muscling

- **Model ID:** `video-effects/muscling`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/video-effects/muscling`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/video-effects/video-effects-muscling

**Request Parameters**

- `image`: string Yes - An image to be used as the start frame of the generated video. Requirements: 1. Only accepts 1 image; 2. Supports realistic style single person photos; 3. Best results when the person is half body, not showing hands, and the cl

### Video Effects Nap Me 360p

- **Model ID:** `video-effects/nap-me-360p`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/video-effects/nap-me-360p`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/video-effects/video-effects-nap-me-360p

**Request Parameters**

- `image`: string Yes - An image to be used as the start frame of the generated video. Requirements: 1. Only accepts 1 image; 2. Only supports single subject and animals; 3. Best for non-solid background with subject facing forward, showing upper or f

### Video Effects Oscar Gala

- **Model ID:** `video-effects/oscar-gala`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/video-effects/oscar-gala`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/video-effects/video-effects-oscar-gala

**Request Parameters**

- `image`: string Yes - An image to be used as the start frame of the generated video. Requirements: 1. Only accepts 1 image; 2. Only single person supported; 3. Better results with headshot photos above the shoulders, without revealing clothes; 4. Im

### Video Effects Paperman

- **Model ID:** `video-effects/paperman`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/video-effects/paperman`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/video-effects/video-effects-paperman

**Request Parameters**

- `image`: string Yes - An image to be used as the start frame of the generated video. Requirements: 1. Only accepts 1 image; 2. Only supports realistic-style single-person photos; 3. Best when more than half of the person's body is visible; 4. Images

### Video Effects Past Life Job

- **Model ID:** `video-effects/past-life-job`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/video-effects/past-life-job`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/video-effects/video-effects-past-life-job

**Request Parameters**

- `image`: string Yes - First frame of the video; Supported image formats include.jpg/.jpeg/.png; The image file size cannot exceed 10MB, and the image resolution should not be less than 300*300px, and the aspect ratio of the image should be between 1
- `resolution`: string No 540p 360p, 540p, 720p Video quality (360p/540p/720p/1080p).

### Video Effects Pet Lovers

- **Model ID:** `video-effects/pet-lovers`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/video-effects/pet-lovers`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/video-effects/video-effects-pet-lovers

**Request Parameters**

- `image`: string Yes - An image to be used as the start frame of the generated video. Requirements: 1. Only accepts 1 image; 2. Only dual-pet photo supported; 3. Clear exposure of two pets' features without overlapping for best results; 4. Images can

### Video Effects Pilot

- **Model ID:** `video-effects/pilot`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/video-effects/pilot`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/video-effects/video-effects-pilot

**Request Parameters**

- `image`: string Yes - An image to be used as the start frame of the generated video. Requirements: 1. Only accepts 1 image; 2. Supports single-person, pet, two-person, or group photos; 3. Best for front upper body shots above waist, with subjects st

### Video Effects Pinch

- **Model ID:** `video-effects/pinch`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/video-effects/pinch`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/video-effects/video-effects-pinch

**Request Parameters**

- `image`: string Yes - An image to be used as the start frame of the generated video. Requirements: 1. Only accepts 1 image; 2. Supports single/dual/multi-subject for people, and single subject for animals and objects; 3. Best for waist-up or full-bo

### Video Effects Pixel Me

- **Model ID:** `video-effects/pixel-me`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/video-effects/pixel-me`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/video-effects/video-effects-pixel-me

**Request Parameters**

- `image`: string Yes - An image to be used as the start frame of the generated video. Requirements: 1. Only accepts 1 image; 2. Supports single-person photos, two-person group photos, or multi-person group photos; 3. Best results when the main subjec

### Video Effects Pubg Winner Hit

- **Model ID:** `video-effects/pubg-winner-hit`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/video-effects/pubg-winner-hit`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/video-effects/video-effects-pubg-winner-hit

**Request Parameters**

- `image`: string Yes - An image to be used as the start frame of the generated video. Requirements: 1. Only accepts 1 image; 2. Only single-person photos are supported; 3. Better results when the main subject is shown facing forward with upper body o
- `bgm`: boolean Yes true - Enable or disable background music for the video.

### Video Effects Rain Kiss

- **Model ID:** `video-effects/rain-kiss`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/video-effects/rain-kiss`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/video-effects/video-effects-rain-kiss

**Request Parameters**

- `image`: string Yes - An image to be used as the start frame of the generated video. Requirements: 1. Only accepts 1 image; 2. Only two-person group photos are supported; 3. Better results when the subjects are facing forward and showing the upper b

### Video Effects Red Or White

- **Model ID:** `video-effects/red-or-white`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/video-effects/red-or-white`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/video-effects/video-effects-red-or-white

**Request Parameters**

- `image`: string Yes - First frame of the video; Supported image formats include.jpg/.jpeg/.png; The image file size cannot exceed 10MB, and the image resolution should not be less than 300*300px, and the aspect ratio of the image should be between 1
- `resolution`: string No 540p 360p, 540p, 720p Video quality (360p/540p/720p/1080p).

### Video Effects Romantic Lift

- **Model ID:** `video-effects/romantic-lift`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/video-effects/romantic-lift`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/video-effects/video-effects-romantic-lift

**Request Parameters**

- `image`: string Yes - An image to be used as the start frame of the generated video. Requirements: 1. Only accepts 1 image; 2. Only supports dual-person photo; 3. Full-body photo of two subjects with visible feet and thighs for better results; 4. Im

### Video Effects Sexy Me

- **Model ID:** `video-effects/sexy-me`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/video-effects/sexy-me`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/video-effects/video-effects-sexy-me

**Request Parameters**

- `image`: string Yes - An image to be used as the start frame of the generated video. Requirements: 1. Only accepts 1 image; 2. Supports solo portraits (female/male) in photorealistic or anime-style only; 3. Optimal results with half-body or full-bod

### Video Effects Shake Dance

- **Model ID:** `video-effects/shake-dance`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/video-effects/shake-dance`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/video-effects/video-effects-shake-dance

**Request Parameters**

- `image`: string Yes - An image to be used as the start frame of the generated video. Requirements: 1. Only accepts 1 image; 2. Supports single person, two-person, or group photos; 3. Better results when the subject is standing, not holding any props

### Video Effects Slice Therapy

- **Model ID:** `video-effects/slice-therapy`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/video-effects/slice-therapy`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/video-effects/video-effects-slice-therapy

**Request Parameters**

- `image`: string Yes - An image to be used as the start frame of the generated video. Requirements: 1. Only accepts 1 image; 2. Only supports single small object as main subject; 3. Clean background with clear and rounded contours for best results; 4

### Video Effects Soul Depart

- **Model ID:** `video-effects/soul-depart`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/video-effects/soul-depart`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/video-effects/video-effects-soul-depart

**Request Parameters**

- `image`: string Yes - An image to be used as the start frame of the generated video. Requirements: 1. Only accepts 1 image; 2. Supports single-person, two-person, group, pet, or object photos; 3. Best when sufficient space is left above the subject 

### Video Effects Split Stance Human

- **Model ID:** `video-effects/split-stance-human`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/video-effects/split-stance-human`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/video-effects/video-effects-split-stance-human

**Request Parameters**

- `image`: string Yes - An image to be used as the start frame of the generated video. Requirements: 1. Only accepts 1 image; 2. Only supports single subject; 3. Best for full-body standing images; 4. Images can be provided via URLs or Base64 encode; 

### Video Effects Squid Game

- **Model ID:** `video-effects/squid-game`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/video-effects/squid-game`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/video-effects/video-effects-squid-game

**Request Parameters**

- `image`: string Yes - An image to be used as the start frame of the generated video. Requirements: 1. Only accepts 1 image; 2. Supports 1-2 people or pets, real and animated; 3. Best for front-facing subjects with clear photos; 4. Images can be prov
- `bgm`: boolean No true - Enable or disable background music. Default is true.

### Video Effects Star Carpet

- **Model ID:** `video-effects/star-carpet`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/video-effects/star-carpet`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/video-effects/video-effects-star-carpet

**Request Parameters**

- `image`: string Yes - An image to be used as the start frame of the generated video. Requirements: 1. Only accepts 1 image; 2. Supports single person and single pet; 3. Best for half-body or full-body human with full-body pet; 4. Images can be provi

### Video Effects Subject 3

- **Model ID:** `video-effects/subject-3`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/video-effects/subject-3`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/video-effects/video-effects-subject-3

**Request Parameters**

- `image`: string Yes - An image to be used as the start frame of the generated video. Requirements: 1. Only accepts 1 image; 2. Only single-person photos are supported; 3. Better results when the main subject is shown facing forward with upper body o
- `bgm`: boolean Yes true - Enable or disable background music for the video.

### Video Effects Sweet Proposal

- **Model ID:** `video-effects/sweet-proposal`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/video-effects/sweet-proposal`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/video-effects/video-effects-sweet-proposal

**Request Parameters**

- `image`: string Yes - An image to be used as the start frame of the generated video. Requirements: 1. Only accepts 1 image; 2. Supports two-person photos only; 3. Best for full-body photos with visible feet and thighs; 4. Images can be provided via 

### Video Effects Tap Me

- **Model ID:** `video-effects/tap-me`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/video-effects/tap-me`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/video-effects/video-effects-tap-me

**Request Parameters**

- `image`: string Yes - An image to be used as the start frame of the generated video. Requirements: 1. Only accepts 1 image; 2. Only supports single photos of male or female subjects; 3. The effect is better when the image contains a single subject; 

### Video Effects Toy Me

- **Model ID:** `video-effects/toy-me`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/video-effects/toy-me`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/video-effects/video-effects-toy-me

**Request Parameters**

- `image`: string Yes - An image to be used as the start frame of the generated video. Requirements: 1. Only accepts 1 image; 2. Supports single-person, pet, two-person, human-pet, or group photos; 3. Better results when the main subject shows the fro

### Video Effects Walk Forward

- **Model ID:** `video-effects/walk-forward`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/video-effects/walk-forward`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/video-effects/video-effects-walk-forward

**Request Parameters**

- `image`: string Yes - An image to be used as the start frame of the generated video. Requirements: 1. Only accepts 1 image; 2. Supports single person photos, two-person photos, or group photos; 3. Best results when the main subject is a single perso

### Video Effects Zoom In Fast

- **Model ID:** `video-effects/zoom-in-fast`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/video-effects/zoom-in-fast`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/video-effects/video-effects-zoom-in-fast

**Request Parameters**

- `image`: string Yes - An image to be used as the start frame of the generated video. Requirements: 1. Only accepts 1 image; 2. Supports product images, scene images, and portraits; 3. Best for medium or wide shots of products and scenes; 4. Images c

### Video Effects Zoom Out

- **Model ID:** `video-effects/zoom-out`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/video-effects/zoom-out`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/video-effects/video-effects-zoom-out

**Request Parameters**

- `image`: string Yes - An image to be used as the start frame of the generated video. Requirements: 1. Only accepts 1 image; 2. Supports product and scene images; 3. Best for close-up product shots or partial scene views; 4. Images can be provided vi

### Vidu Image To Video

- **Model ID:** `vidu/image-to-video`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/vidu/image-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/vidu/vidu-image-to-video

**Request Parameters**

- `image`: string Yes - The start image for generating the output.
- `prompt`: string Yes - The positive prompt for the generation.
- `movement_amplitude`: string No auto auto, small, medium, large The movement amplitude of objects in the frame. Defaults to auto, accepted value: auto small medium large.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Vidu Image To Video 2.0

- **Model ID:** `vidu/image-to-video-2.0`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/vidu/image-to-video-2.0`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/vidu/vidu-image-to-video-2.0

**Request Parameters**

- `image`: string Yes - An image to be used as the start frame of the generated video. For fields that accept images: Only accepts 1 image; Images Assets can be provided via URLs or Base64 encode; You must use one of the following codecs: PNG, JPEG, J
- `prompt`: string Yes - The positive prompt for the generation.
- `movement_amplitude`: string No auto auto, small, medium, large The movement amplitude of objects in the frame. Defaults to auto, accepted value: auto small medium large.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation.

### Vidu Image To Video Q1

- **Model ID:** `vidu/image-to-video-q1`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/vidu/image-to-video-q1`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/vidu/vidu-image-to-video-q1

**Request Parameters**

- `image`: string Yes - The start image for generating the output.
- `prompt`: string Yes - The positive prompt for the generation.
- `movement_amplitude`: string No auto auto, small, medium, large The movement amplitude of objects in the frame. Defaults to auto, accepted value: auto small medium large.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Vidu Image To Video Q2 Pro

- **Model ID:** `vidu/image-to-video-q2-pro`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/vidu/image-to-video-q2-pro`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/vidu/vidu-image-to-video-q2-pro

**Request Parameters**

- `image`: string Yes - The start image for generating the output.
- `prompt`: string Yes - The positive prompt for the generation.
- `duration`: integer No 5 1, 2, 3, 4, 5, 6, 7, 8, 9, 10 The duration of the generated media in seconds.
- `resolution`: string No 720p 540p, 720p, 1080p Video resolution.
- `bgm`: boolean No true - The background music for generating the output.
- `movement_amplitude`: string No auto auto, small, medium, large The movement amplitude of objects in the frame. Defaults to auto, accepted value: auto small medium large.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Vidu Image To Video Q2 Turbo

- **Model ID:** `vidu/image-to-video-q2-turbo`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/vidu/image-to-video-q2-turbo`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/vidu/vidu-image-to-video-q2-turbo

**Request Parameters**

- `image`: string Yes - The start image for generating the output.
- `prompt`: string Yes - The positive prompt for the generation.
- `duration`: integer No 5 1, 2, 3, 4, 5, 6, 7, 8, 9, 10 The duration of the generated media in seconds.
- `resolution`: string No 720p 540p, 720p, 1080p Video resolution.
- `bgm`: boolean No true - The background music for generating the output.
- `movement_amplitude`: string No auto auto, small, medium, large The movement amplitude of objects in the frame. Defaults to auto, accepted value: auto small medium large.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Vidu Q2 Pro Extend Video

- **Model ID:** `vidu/q2-pro/extend-video`
- **Operation:** `video_extend`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/vidu/q2-pro/extend-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/vidu/vidu-q2-pro-extend-video

**Request Parameters**

- `video`: string Yes - The video's duration cannot be less than 4 seconds and cannot exceed 1 minute.
- `image`: string No - The model will use the image passed in this parameter as the final frame to extend the video.The aspect ratio of the images must be less than 1:4 or 4:1
- `prompt`: string No - The positive prompt for the generation.
- `duration`: number No 5 1 ~ 7 Extended duration.
- `resolution`: string No 720p 540p, 720p, 1080p The resolution of the generated media.

### Vidu Q2 Pro Image To Video Fast

- **Model ID:** `vidu/q2-pro/image-to-video-fast`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/vidu/q2-pro/image-to-video-fast`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/vidu/vidu-q2-pro-image-to-video-fast

**Request Parameters**

- `image`: string Yes - The input image for generating the video.
- `prompt`: string Yes - The positive prompt for the generation.
- `duration`: number No 5 1 ~ 8 The duration of the generated video in seconds.
- `resolution`: string No 720p 720p, 1080p Video resolution.
- `bgm`: boolean No true - Whether to add background music to the output.
- `movement_amplitude`: string No auto auto, small, medium, large The movement amplitude of objects in the frame.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed. -1 means a random seed will be used.

### Vidu Q2 Pro Start End To Video Fast

- **Model ID:** `vidu/q2-pro/start-end-to-video-fast`
- **Operation:** `frame_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/vidu/q2-pro/start-end-to-video-fast`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/vidu/vidu-q2-pro-start-end-to-video-fast

**Request Parameters**

- `image`: string Yes - The start image for generating the video.
- `prompt`: string Yes - The positive prompt for the generation.
- `last_image`: string Yes - - The end image for generating the video.
- `duration`: number No 5 1 ~ 8 The duration of the generated video in seconds.
- `resolution`: string No 720p 720p, 1080p Video resolution.
- `bgm`: boolean No true - Whether to add background music to the output.
- `movement_amplitude`: string No auto auto, small, medium, large The movement amplitude of objects in the frame.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed. -1 means a random seed will be used.

### Vidu Q2 Turbo Extend Video

- **Model ID:** `vidu/q2-turbo/extend-video`
- **Operation:** `video_extend`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/vidu/q2-turbo/extend-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/vidu/vidu-q2-turbo-extend-video

**Request Parameters**

- `video`: string Yes - The video's duration cannot be less than 4 seconds and cannot exceed 1 minute.
- `image`: string No - The model will use the image passed in this parameter as the final frame to extend the video.The aspect ratio of the images must be less than 1:4 or 4:1
- `prompt`: string No - The positive prompt for the generation.
- `duration`: number No 5 1 ~ 7 Extended duration.
- `resolution`: string No 720p 540p, 720p, 1080p The resolution of the generated media.

### Vidu Q3 Turbo Image To Video

- **Model ID:** `vidu/q3-turbo/image-to-video`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/vidu/q3-turbo/image-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/vidu/vidu-q3-turbo-image-to-video

**Request Parameters**

- `image`: string Yes - The URL of the image to generate an image from.
- `prompt`: string Yes - The positive prompt for the generation.
- `resolution`: string No 720p 540p, 720p, 1080p The resolution of the generated media.
- `duration`: number No 5 1 ~ 16 The duration of the generated media in seconds.
- `movement_amplitude`: string No auto auto, small, medium, large The movement amplitude of objects in the frame. Defaults to auto, accepted value: auto small medium large.
- `generate_audio`: boolean No true - Whether to generate audio.
- `bgm`: boolean No true - The background music for generating the output.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Vidu Q3 Turbo Start End To Video

- **Model ID:** `vidu/q3-turbo/start-end-to-video`
- **Operation:** `frame_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/vidu/q3-turbo/start-end-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/vidu/vidu-q3-turbo-start-end-to-video

**Request Parameters**

- `image`: string Yes - The start image for generating the output.
- `prompt`: string Yes - The positive prompt for the generation.
- `last_image`: string Yes - - The end image for generating the output.
- `duration`: number No 5 1 ~ 16 The duration of the generated media in seconds.
- `resolution`: string No 720p 540p, 720p, 1080p The resolution of the generated media.
- `bgm`: boolean No true - The background music for generating the output.
- `generate_audio`: boolean No true - Whether to generate audio.
- `movement_amplitude`: string No auto auto, small, medium, large The movement amplitude of objects in the frame. Defaults to auto, accepted value: auto small medium large.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Vidu Q3 Turbo Text To Video

- **Model ID:** `vidu/q3-turbo/text-to-video`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/vidu/q3-turbo/text-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/vidu/vidu-q3-turbo-text-to-video

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `style`: string No general general, anime The style of output video.
- `resolution`: string No 720p 540p, 720p, 1080p The resolution of the generated media.
- `duration`: number No 5 1 ~ 16 The duration of the generated media in seconds.
- `movement_amplitude`: string No auto auto, small, medium, large The movement amplitude of objects in the frame. Defaults to auto, accepted value: auto small medium large.
- `generate_audio`: boolean No true - Whether to generate audio.
- `bgm`: boolean No true - The background music for generating the output.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Vidu Q3 Image To Video

- **Model ID:** `vidu/q3/image-to-video`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/vidu/q3/image-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/vidu/vidu-q3-image-to-video

**Request Parameters**

- `image`: string Yes - The URL of the image to generate an image from.
- `prompt`: string Yes - The positive prompt for the generation.
- `resolution`: string No 720p 540p, 720p, 1080p The resolution of the generated media.
- `duration`: number No 5 1 ~ 16 The duration of the generated media in seconds.
- `movement_amplitude`: string No auto auto, small, medium, large The movement amplitude of objects in the frame. Defaults to auto, accepted value: auto small medium large.
- `generate_audio`: boolean No true - Whether to generate audio.
- `bgm`: boolean No true - The background music for generating the output.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Vidu Q3 Image To Video Pro

- **Model ID:** `vidu/q3/image-to-video-pro`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/vidu/q3/image-to-video-pro`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/vidu/vidu-q3-image-to-video-pro

**Request Parameters**

- `image`: string Yes - The URL of the image to generate an image from.
- `prompt`: string Yes - The positive prompt for the generation.
- `resolution`: string No 720p 720p, 1080p, 2k, 4k The resolution of the generated media.
- `duration`: number No 5 1 ~ 16 The duration of the generated media in seconds.
- `movement_amplitude`: string No auto auto, small, medium, large The movement amplitude of objects in the frame. Defaults to auto, accepted value: auto small medium large.
- `generate_audio`: boolean No true - Whether to generate audio.
- `bgm`: boolean No true - The background music for generating the output.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Vidu Q3 Image To Video Spicy

- **Model ID:** `vidu/q3/image-to-video-spicy`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/vidu/q3/image-to-video-spicy`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/vidu/vidu-q3-image-to-video-spicy

**Request Parameters**

- `image`: string Yes - The URL of the image to generate an image from.
- `prompt`: string No - The positive prompt for the generation.
- `resolution`: string No 720p 540p, 720p, 1080p The resolution of the generated media.
- `duration`: number No 5 1 ~ 16 The duration of the generated media in seconds.
- `movement_amplitude`: string No auto auto, small, medium, large The movement amplitude of objects in the frame. Defaults to auto, accepted value: auto small medium large.
- `generate_audio`: boolean No true - Whether to generate audio.
- `bgm`: boolean No true - The background music for generating the output.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Vidu Q3 Reference To Video

- **Model ID:** `vidu/q3/reference-to-video`
- **Operation:** `reference_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/vidu/q3/reference-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/vidu/vidu-q3-reference-to-video

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `images`: array Yes [] 1 ~ 4 items Reference images for video generation. Requirements: 1. Accept 1-4 images; 2. Images can be URLs or Base64 encoded
- `aspect_ratio`: string No 16:9 16:9, 9:16, 4:3, 3:4, 1:1 The aspect ratio of the generated media.
- `resolution`: string No 720p 360p, 540p, 720p, 1080p The resolution of the generated media.
- `duration`: number No 5 1 ~ 16 The duration of the generated media in seconds.
- `generate_audio`: boolean No true -
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation.

### Vidu Q3 Start End To Video

- **Model ID:** `vidu/q3/start-end-to-video`
- **Operation:** `frame_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/vidu/q3/start-end-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/vidu/vidu-q3-start-end-to-video

**Request Parameters**

- `image`: string Yes - The start image for generating the output.
- `prompt`: string Yes - The positive prompt for the generation.
- `last_image`: string Yes - - The end image for generating the output.
- `duration`: number No 5 1 ~ 16 The duration of the generated media in seconds.
- `resolution`: string No 720p 540p, 720p, 1080p The resolution of the generated media.
- `bgm`: boolean No true - The background music for generating the output.
- `generate_audio`: boolean No true - Whether to generate audio.
- `movement_amplitude`: string No auto auto, small, medium, large The movement amplitude of objects in the frame. Defaults to auto, accepted value: auto small medium large.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Vidu Q3 Text To Video

- **Model ID:** `vidu/q3/text-to-video`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/vidu/q3/text-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/vidu/vidu-q3-text-to-video

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `style`: string No general general, anime The style of output video.
- `resolution`: string No 720p 540p, 720p, 1080p The resolution of the generated media.
- `duration`: number No 5 1 ~ 16 The duration of the generated media in seconds.
- `aspect_ratio`: string No 4:3 16:9, 9:16, 4:3, 3:4, 1:1 The aspect ratio of the generated media.
- `movement_amplitude`: string No auto auto, small, medium, large The movement amplitude of objects in the frame. Defaults to auto, accepted value: auto small medium large.
- `generate_audio`: boolean No true - Whether to generate audio.
- `bgm`: boolean No true - The background music for generating the output.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Vidu Reference To Video 2.0

- **Model ID:** `vidu/reference-to-video-2.0`
- **Operation:** `reference_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/vidu/reference-to-video-2.0`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/vidu/vidu-reference-to-video-2.0

**Request Parameters**

- `images`: array Yes [] 1 ~ 3 items The model will use the provided images as references to generate a video with consistent subjects. For fields that accept images: Accepts 1 to 3 images; Images Assets can be provided via URLs or Base64 encode; You m
- `prompt`: string Yes - The positive prompt for the generation.
- `aspect_ratio`: string No 16:9 16:9, 9:16, 1:1 The aspect ratio of the generated media.
- `movement_amplitude`: string No auto auto, small, medium, large The movement amplitude of objects in the frame. Defaults to auto, accepted value: auto, small, medium, large.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation.

### Vidu Reference To Video Q1

- **Model ID:** `vidu/reference-to-video-q1`
- **Operation:** `reference_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/vidu/reference-to-video-q1`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/vidu/vidu-reference-to-video-q1

**Request Parameters**

- `images`: array Yes [] 1 ~ 7 items Reference images for video generation. Requirements: 1. Accept 1-3 images; 2. Images can be URLs or Base64 encoded; 3. Supported formats: PNG, JPEG, JPG, WebP; 4. Minimum size: 128*128 pixels; 5. Aspect ratio: less 
- `prompt`: string Yes - The positive prompt for the generation.
- `aspect_ratio`: string No 16:9 16:9, 9:16, 1:1 The aspect ratio of the generated media.
- `movement_amplitude`: string No auto auto, small, medium, large The movement amplitude of objects in the frame. Defaults to auto, accepted value: auto, small, medium, large.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation.

### Vidu Reference To Video Q2

- **Model ID:** `vidu/reference-to-video-q2`
- **Operation:** `reference_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/vidu/reference-to-video-q2`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/vidu/vidu-reference-to-video-q2

**Request Parameters**

- `images`: array Yes [] 1 ~ 7 items Reference images for video generation. Requirements: 1. Accept 1-7 images; 2. Images can be URLs or Base64 encoded
- `prompt`: string Yes - The positive prompt for the generation.
- `aspect_ratio`: string No 16:9 16:9, 9:16, 4:3, 3:4, 1:1 The aspect ratio of the generated media.
- `resolution`: string No 720p 540p, 720p, 1080p The resolution of the generated media.
- `duration`: number No 5 1 ~ 10 The duration of the generated media in seconds.
- `movement_amplitude`: string No auto auto, small, medium, large The movement amplitude of objects in the frame. Defaults to auto, accepted value: auto, small, medium, large.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation.

### Vidu Start End To Video

- **Model ID:** `vidu/start-end-to-video`
- **Operation:** `frame_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/vidu/start-end-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/vidu/vidu-start-end-to-video

**Request Parameters**

- `image`: string Yes - The start image for generating the output.
- `prompt`: string Yes - The positive prompt for the generation.
- `last_image`: string Yes - - The end image for generating the output.
- `movement_amplitude`: string No auto auto, small, medium, large The movement amplitude of objects in the frame. Defaults to auto, accepted value: auto small medium large.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Vidu Start End To Video 2.0

- **Model ID:** `vidu/start-end-to-video-2.0`
- **Operation:** `frame_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/vidu/start-end-to-video-2.0`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/vidu/vidu-start-end-to-video-2.0

**Request Parameters**

- `image`: string Yes - The start image for generating the output.
- `prompt`: string Yes - The positive prompt for the generation.
- `last_image`: string Yes - - The end image for generating the output.
- `movement_amplitude`: string No auto auto, small, medium, large The movement amplitude of objects in the frame. Defaults to auto, accepted value: auto small medium large.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Vidu Start End To Video Q1

- **Model ID:** `vidu/start-end-to-video-q1`
- **Operation:** `frame_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/vidu/start-end-to-video-q1`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/vidu/vidu-start-end-to-video-q1

**Request Parameters**

- `image`: string Yes - The start image for generating the output.
- `prompt`: string Yes - The positive prompt for the generation.
- `last_image`: string Yes - - The end image for generating the output.
- `movement_amplitude`: string No auto auto, small, medium, large The movement amplitude of objects in the frame. Defaults to auto, accepted value: auto small medium large.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Vidu Start End To Video Q2 Pro

- **Model ID:** `vidu/start-end-to-video-q2-pro`
- **Operation:** `frame_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/vidu/start-end-to-video-q2-pro`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/vidu/vidu-start-end-to-video-q2-pro

**Request Parameters**

- `image`: string Yes - The start image for generating the output.
- `prompt`: string Yes - The positive prompt for the generation.
- `last_image`: string Yes - - The end image for generating the output.
- `duration`: number No 5 1 ~ 8 The duration of the generated media in seconds.
- `resolution`: string No 720p 720p, 1080p Video resolution.
- `bgm`: boolean No true - The background music for generating the output.
- `movement_amplitude`: string No auto auto, small, medium, large The movement amplitude of objects in the frame. Defaults to auto, accepted value: auto small medium large.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Vidu Start End To Video Q2 Turbo

- **Model ID:** `vidu/start-end-to-video-q2-turbo`
- **Operation:** `frame_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/vidu/start-end-to-video-q2-turbo`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/vidu/vidu-start-end-to-video-q2-turbo

**Request Parameters**

- `image`: string Yes - The start image for generating the output.
- `prompt`: string Yes - The positive prompt for the generation.
- `last_image`: string Yes - - The end image for generating the output.
- `duration`: number No 5 1 ~ 10 The duration of the generated media in seconds.
- `resolution`: string No 720p 720p, 1080p Video resolution.
- `bgm`: boolean No true - The background music for generating the output.
- `movement_amplitude`: string No auto auto, small, medium, large The movement amplitude of objects in the frame. Defaults to auto, accepted value: auto small medium large.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Vidu Text To Video

- **Model ID:** `vidu/text-to-video`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/vidu/text-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/vidu/vidu-text-to-video

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `movement_amplitude`: string No auto auto, small, medium, large The movement amplitude of objects in the frame. Defaults to auto, accepted value: auto small medium large.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Vidu Text To Video 2.0

- **Model ID:** `vidu/text-to-video-2.0`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/vidu/text-to-video-2.0`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/vidu/vidu-text-to-video-2.0

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `duration`: integer No 5 5, 8 The duration of the generated media in seconds.
- `resolution`: string No 720p 720p The resolution of the generated media.
- `movement_amplitude`: string No auto auto, small, medium, large The movement amplitude of objects in the frame. Defaults to auto, accepted value: auto small medium large.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Vidu Text To Video Q1

- **Model ID:** `vidu/text-to-video-q1`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/vidu/text-to-video-q1`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/vidu/vidu-text-to-video-q1

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `movement_amplitude`: string No auto auto, small, medium, large The movement amplitude of objects in the frame. Defaults to auto, accepted value: auto small medium large.
- `style`: string No general general, anime The style of output video.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Vidu Text To Video Q2

- **Model ID:** `vidu/text-to-video-q2`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/vidu/text-to-video-q2`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/vidu/vidu-text-to-video-q2

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `style`: string No general general, anime The style of output video.
- `resolution`: string No 720p 540p, 720p, 1080p The resolution of the generated media.
- `duration`: number No 5 1 ~ 10 The duration of the generated media in seconds.
- `aspect_ratio`: string No 4:3 3:4, 4:3 The aspect ratio of the generated media.
- `movement_amplitude`: string No auto auto, small, medium, large The movement amplitude of objects in the frame. Defaults to auto, accepted value: auto small medium large.
- `bgm`: boolean No true - The background music for generating the output.
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### AI Dog Selfie Video

- **Model ID:** `wavespeed-ai/ai-dog-selfie-video`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/ai-dog-selfie-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/ai-dog-selfie-video

**Request Parameters**

- `image`: string Yes - The URL of the input image (optional).
- `breed`: string No random random, golden_retriever, husky, corgi, poodle, labrador, shiba, pomeranian, bulldog, dalmatian, samoyed Dog breed. Choose from presets or enter a custom breed.
- `count`: integer No 1 1 ~ 5 Number of videos to generate (1-5).
- `dog_size`: string No any any, puppy, adult Dog age: puppy or adult.
- `style`: string No casual casual, studio, outdoor, christmas, beach, cozy Photo style.
- `expression`: string No happy happy, silly, cool, sleeping Dog expression.
- `action`: string No play lick, hug, play, shake_hands Dog action in the video.
- `duration`: integer No 5 5 ~ 15 Video duration in seconds (5-15).

### AI Parkour Video

- **Model ID:** `wavespeed-ai/ai-parkour-video`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/ai-parkour-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/ai-parkour-video

**Request Parameters**

- `image`: string Yes - The URL of the input image (required).
- `video`: string No - Reference parkour video URL. When provided, uses video-to-video animation (720p). When omitted, generates from image.
- `style`: string No rooftop rooftop, urban, wall_run, flip, forest, stairs Parkour style preset.
- `duration`: integer No 5 5, 10, 15 The duration of the generated video in seconds. Used when video is not provided.

### AI Sketch To Video

- **Model ID:** `wavespeed-ai/ai-sketch-to-video`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/ai-sketch-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/ai-sketch-to-video

**Request Parameters**

- `image`: string Yes - The URL of the input sketch image.
- `duration`: integer No 5 5 ~ 15 Video duration in seconds (5-15).

### AI Video Ads

- **Model ID:** `wavespeed-ai/ai-video-ads`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/ai-video-ads`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/ai-video-ads

**Request Parameters**

- `image`: string Yes - Person photo URL (required).
- `product_name`: string Yes - - Product name (required).
- `product_image`: string No - - Product photo URL. Provides more accurate compositing when available.
- `text`: string No - - Script or selling points for LLM reference (optional).
- `language`: string No - en, zh, es, fr, pt Language of the generated ad script.
- `duration`: integer No 5 5 ~ 15 The duration of the generated video in seconds.

### Cinematic Video Generator

- **Model ID:** `wavespeed-ai/cinematic-video-generator`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/cinematic-video-generator`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/cinematic-video-generator

**Request Parameters**

- `prompt`: string Yes - Describe the scene, action, camera movement, and mood for the video.
- `images`: array No [] - Optional reference images (up to 4) to guide the visual style, characters, or scene composition.
- `aspect_ratio`: string No 16:9 16:9, 9:16, 4:3, 3:4 The aspect ratio of the generated video.
- `duration`: integer No 5 5, 10, 15 The duration of the generated video in seconds.

### Cosmos Predict 2.5 Image To Video

- **Model ID:** `wavespeed-ai/cosmos-predict-2.5/image-to-video`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/cosmos-predict-2.5/image-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/cosmos-predict-2.5-image-to-video

**Request Parameters**

- `prompt`: string Yes - Text prompt describing the motion, action, and style you want in the generated video
- `image`: string Yes - URL of the input image to use as the first frame of the video

### Cosmos Predict 2.5 Text To Video

- **Model ID:** `wavespeed-ai/cosmos-predict-2.5/text-to-video`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/cosmos-predict-2.5/text-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/cosmos-predict-2.5-text-to-video

**Request Parameters**

- `prompt`: string Yes - Text prompt describing the scene, action, and visual style you want in the generated video

### Davinci Magihuman Image To Video

- **Model ID:** `wavespeed-ai/davinci-magihuman/image-to-video`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/davinci-magihuman/image-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/davinci-magihuman-image-to-video

**Request Parameters**

- `image`: string Yes - The image for generating the output.
- `prompt`: string Yes - The positive prompt for the generation.
- `audio`: string No - - The audio URL for generating the output. If provided, the model will generate a video synchronized with the audio.
- `aspect_ratio`: string No 16:9 16:9, 9:16 The aspect ratio of the generated video.
- `resolution`: string No 720p 256p, 720p, 1080p The resolution of the generated video.
- `duration`: integer No 5 5, 6, 7, 8, 9, 10 The duration of the generated video in seconds.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Davinci Magihuman Text To Video

- **Model ID:** `wavespeed-ai/davinci-magihuman/text-to-video`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/davinci-magihuman/text-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/davinci-magihuman-text-to-video

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `audio`: string No - - The audio URL for generating the output. If provided, the model will generate a video synchronized with the audio.
- `aspect_ratio`: string No 16:9 16:9, 9:16 The aspect ratio of the generated video.
- `resolution`: string No 720p 256p, 720p, 1080p The resolution of the generated video.
- `duration`: integer No 5 5, 6, 7, 8, 9, 10 The duration of the generated video in seconds.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Depth Anything Video

- **Model ID:** `wavespeed-ai/depth-anything/video`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/depth-anything/video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/depth-anything-video

**Request Parameters**

- `video`: string Yes - The URL of the input video to estimate depth for.
- `model`: string No VDA-Large VDA-Small, VDA-Base, VDA-Large Depth estimation model size. VDA-Large for best quality, VDA-Small for fastest speed.

### Ghibli Filter Video

- **Model ID:** `wavespeed-ai/ghibli-filter/video`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/ghibli-filter/video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/ghibli-filter-video

**Request Parameters**

- `image`: string Yes - The URL of the input image.
- `duration`: integer No 5 5 ~ 15 Video duration in seconds (5-15).

### Hunyuan Video 1.5 Image To Video

- **Model ID:** `wavespeed-ai/hunyuan-video-1.5/image-to-video`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/hunyuan-video-1.5/image-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/hunyuan-video-1.5-image-to-video

**Request Parameters**

- `image`: string Yes - The image to generate the video from. Provide URL or base64 encoded image.
- `prompt`: string No - The positive prompt for the generation. Describes the motion and action in the video.
- `resolution`: string No 720p 480p, 720p The resolution of the generated video.
- `duration`: integer No 5 5, 8 The duration of the generated video in seconds.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Hunyuan Video 1.5 Text To Video

- **Model ID:** `wavespeed-ai/hunyuan-video-1.5/text-to-video`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/hunyuan-video-1.5/text-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/hunyuan-video-1.5-text-to-video

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `negative_prompt`: string No - The negative prompt for the generation.
- `size`: string No 1280*720 832*480, 480*832, 1280*720, 720*1280 The size of the generated video in pixels (width*height).
- `duration`: integer No 5 5, 8 The duration of the generated video in seconds.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Hunyuan Video Foley

- **Model ID:** `wavespeed-ai/hunyuan-video-foley`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/hunyuan-video-foley`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/hunyuan-video-foley

**Request Parameters**

- `video`: string Yes - The video for generating the output.
- `prompt`: string No - The positive prompt for the generation.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Hunyuan Video I2V

- **Model ID:** `wavespeed-ai/hunyuan-video/i2v`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/hunyuan-video/i2v`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/hunyuan-video-i2v

**Request Parameters**

- `image`: string Yes - The image to generate the video from.
- `prompt`: string No - The positive prompt for the generation.
- `num_inference_steps`: integer No 30 1 ~ 30 The number of inference steps to perform.
- `duration`: integer No 5 5 ~ 10 The duration of the generated media in seconds.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `size`: string No 1280*720 1280*720, 720*1280 The size of the generated media in pixels (width*height).

### Hunyuan Video T2V

- **Model ID:** `wavespeed-ai/hunyuan-video/t2v`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/hunyuan-video/t2v`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/hunyuan-video-t2v

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 1280*720 1280*720, 720*1280 The size of the generated media in pixels (width*height).
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.
- `num_inference_steps`: integer No 30 2 ~ 30 The number of inference steps to perform.

### Infinitetalk Fast Video To Video

- **Model ID:** `wavespeed-ai/infinitetalk-fast/video-to-video`
- **Operation:** `video_edit`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/infinitetalk-fast/video-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/infinitetalk-fast-video-to-video

**Request Parameters**

- `audio`: string Yes - - The audio for generating the output.
- `video`: string Yes - The video for generating the output.
- `mask_image`: string No - Optional mask image to specify the person in the video to animate.
- `prompt`: string No - The positive prompt for the generation.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Infinitetalk Fast Video To Video Multi

- **Model ID:** `wavespeed-ai/infinitetalk-fast/video-to-video-multi`
- **Operation:** `video_edit`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/infinitetalk-fast/video-to-video-multi`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/infinitetalk-fast-video-to-video-multi

**Request Parameters**

- `video`: string Yes - The video for generating the output.
- `left_audio`: string Yes - - The audio of the persion on the left for generating the output.
- `right_audio`: string Yes - - The audio of the persion on the right for generating the output.
- `prompt`: string No - The positive prompt for the generation.
- `order`: string No meanwhile meanwhile, left_right, right_left The order of the two audio sources in the output video, "meanwhile" means both audio sources will play at the same time, "left_right" means the left audio will play first then the right 
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Infinitetalk Video To Video

- **Model ID:** `wavespeed-ai/infinitetalk/video-to-video`
- **Operation:** `video_edit`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/infinitetalk/video-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/infinitetalk-video-to-video

**Request Parameters**

- `audio`: string Yes - - The audio for generating the output.
- `video`: string Yes - The video for generating the output.
- `mask_image`: string No - Optional mask image to specify the person in the video to animate.
- `prompt`: string No - The positive prompt for the generation.
- `resolution`: string No 480p 480p, 720p The resolution of the output video.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Infinitetalk Video To Video Multi

- **Model ID:** `wavespeed-ai/infinitetalk/video-to-video-multi`
- **Operation:** `video_edit`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/infinitetalk/video-to-video-multi`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/infinitetalk-video-to-video-multi

**Request Parameters**

- `video`: string Yes - The video for generating the output.
- `left_audio`: string Yes - - The audio of the persion on the left for generating the output.
- `right_audio`: string Yes - - The audio of the persion on the right for generating the output.
- `prompt`: string No - The positive prompt for the generation.
- `order`: string No meanwhile meanwhile, left_right, right_left The order of the two audio sources in the output video, "meanwhile" means both audio sources will play at the same time, "left_right" means the left audio will play first then the right 
- `resolution`: string No 480p 480p, 720p The resolution of the output video.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Kandinsky5 Pro Image To Video

- **Model ID:** `wavespeed-ai/kandinsky5-pro/image-to-video`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/kandinsky5-pro/image-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/kandinsky5-pro-image-to-video

**Request Parameters**

- `image`: string Yes - The URL of the image to use as a reference for the video generation.
- `prompt`: string Yes - The prompt to generate the video from.
- `resolution`: string No 512p 512p, 1024p Video resolution.
- `duration`: integer No 5 5 The duration of the generated media in seconds.

### Kandinsky5 Pro Text To Video

- **Model ID:** `wavespeed-ai/kandinsky5-pro/text-to-video`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/kandinsky5-pro/text-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/kandinsky5-pro-text-to-video

**Request Parameters**

- `prompt`: string Yes - The text prompt to guide video generation.
- `resolution`: string No 512p 512p, 1024p Video resolution quality.
- `aspect_ratio`: string No 3:2 3:2, 1:1, 2:3 Aspect ratio of the generated video.
- `duration`: integer No 5 5 Video duration in seconds (currently only 5s is supported).

### Ltx 2 19b Image To Video

- **Model ID:** `wavespeed-ai/ltx-2-19b/image-to-video`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/ltx-2-19b/image-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/ltx-2-19b-image-to-video

**Request Parameters**

- `image`: string Yes - The image for the generation.
- `prompt`: string Yes - The positive prompt for the generation.
- `resolution`: string No 720p 480p, 720p, 1080p Video resolution.
- `duration`: integer No 5 5 ~ 20 The duration of the generated media in seconds.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Ltx 2 19b Image To Video LoRA

- **Model ID:** `wavespeed-ai/ltx-2-19b/image-to-video-lora`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/ltx-2-19b/image-to-video-lora`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/ltx-2-19b-image-to-video-lora

**Request Parameters**

- `image`: string Yes - The image for the generation.
- `prompt`: string Yes - The positive prompt for the generation.
- `resolution`: string No 720p 480p, 720p, 1080p Video resolution.
- `duration`: integer No 5 5 ~ 20 The duration of the generated media in seconds.
- `loras`: array No max 3 items List of LoRAs to apply (max 3). loras[].path string Yes - Path to the LoRA model loras[].scale float Yes - 0.0 ~ 4.0 Scale of the LoRA model
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Ltx 2 19b Text To Video

- **Model ID:** `wavespeed-ai/ltx-2-19b/text-to-video`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/ltx-2-19b/text-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/ltx-2-19b-text-to-video

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `resolution`: string No 720p 480p, 720p, 1080p Video resolution.
- `aspect_ratio`: string No 16:9 16:9, 9:16 Aspect ratio of the video.
- `duration`: integer No 5 5 ~ 20 The duration of the generated media in seconds.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Ltx 2 19b Text To Video LoRA

- **Model ID:** `wavespeed-ai/ltx-2-19b/text-to-video-lora`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/ltx-2-19b/text-to-video-lora`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/ltx-2-19b-text-to-video-lora

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `resolution`: string No 720p 480p, 720p, 1080p Video resolution.
- `aspect_ratio`: string No 16:9 16:9, 9:16 Aspect ratio of the video.
- `duration`: integer No 5 5 ~ 20 The duration of the generated media in seconds.
- `loras`: array No max 3 items List of LoRAs to apply (max 3). loras[].path string Yes - Path to the LoRA model loras[].scale float Yes - 0.0 ~ 4.0 Scale of the LoRA model
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Ltx 2.3 Image To Video

- **Model ID:** `wavespeed-ai/ltx-2.3/image-to-video`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/ltx-2.3/image-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/ltx-2.3-image-to-video

**Request Parameters**

- `image`: string Yes - The image for the generation.
- `prompt`: string Yes - The positive prompt for the generation.
- `resolution`: string No 720p 480p, 720p, 1080p Video resolution.
- `duration`: integer No 5 5 ~ 20 The duration of the generated media in seconds.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Ltx 2.3 Image To Video LoRA

- **Model ID:** `wavespeed-ai/ltx-2.3/image-to-video-lora`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/ltx-2.3/image-to-video-lora`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/ltx-2.3-image-to-video-lora

**Request Parameters**

- `image`: string Yes - The image for the generation.
- `prompt`: string Yes - The positive prompt for the generation.
- `resolution`: string No 720p 480p, 720p, 1080p Video resolution.
- `duration`: integer No 5 5 ~ 20 The duration of the generated media in seconds.
- `loras`: array No max 3 items List of LoRAs to apply (max 3). loras[].path string Yes - Path to the LoRA model loras[].scale float Yes - 0.0 ~ 4.0 Scale of the LoRA model
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Ltx 2.3 Text To Video

- **Model ID:** `wavespeed-ai/ltx-2.3/text-to-video`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/ltx-2.3/text-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/ltx-2.3-text-to-video

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `resolution`: string No 720p 480p, 720p, 1080p Video resolution.
- `aspect_ratio`: string No 16:9 16:9, 9:16 Aspect ratio of the video.
- `duration`: integer No 5 5 ~ 20 The duration of the generated media in seconds.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Ltx 2.3 Text To Video LoRA

- **Model ID:** `wavespeed-ai/ltx-2.3/text-to-video-lora`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/ltx-2.3/text-to-video-lora`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/ltx-2.3-text-to-video-lora

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `resolution`: string No 720p 480p, 720p, 1080p Video resolution.
- `aspect_ratio`: string No 16:9 16:9, 9:16 Aspect ratio of the video.
- `duration`: integer No 5 5 ~ 20 The duration of the generated media in seconds.
- `loras`: array No max 3 items List of LoRAs to apply (max 3). loras[].path string Yes - Path to the LoRA model loras[].scale float Yes - 0.0 ~ 4.0 Scale of the LoRA model
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Ltx 2.3 Video Extend

- **Model ID:** `wavespeed-ai/ltx-2.3/video-extend`
- **Operation:** `video_extend`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/ltx-2.3/video-extend`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/ltx-2.3-video-extend

**Request Parameters**

- `video`: string Yes - The video for the extension.
- `duration`: integer No 5 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20 The duration of the generated media in seconds.
- `prompt`: string No - The positive prompt for the extension.

### Ltx 2 Video Extend

- **Model ID:** `wavespeed-ai/ltx-2/video-extend`
- **Operation:** `video_extend`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/ltx-2/video-extend`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/ltx-2-video-extend

**Request Parameters**

- `video`: string Yes - The URL of the video to extend.
- `prompt`: string No - Description of what should happen in the extended portion of the video.
- `duration`: number No 5 1 ~ 20 Duration in seconds to extend the video. Maximum 20 seconds.

### Ltx Video V097 I2V 480p

- **Model ID:** `wavespeed-ai/ltx-video-v097/i2v-480p`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/ltx-video-v097/i2v-480p`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/ltx-video-v097-i2v-480p

**Request Parameters**

- `image`: string Yes - Image URL for Image-to-Video task.
- `prompt`: string Yes - The positive prompt for the generation.
- `negative_prompt`: string No - The negative prompt for the generation.
- `size`: string No 832*480 832*480, 480*832 The size of the generated media in pixels (width*height).
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation.

### Ltx Video V097 I2V 720p

- **Model ID:** `wavespeed-ai/ltx-video-v097/i2v-720p`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/ltx-video-v097/i2v-720p`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/ltx-video-v097-i2v-720p

**Request Parameters**

- `image`: string Yes - Image URL for Image-to-Video task.
- `prompt`: string Yes - The positive prompt for the generation.
- `negative_prompt`: string No - The negative prompt for the generation.
- `size`: string No 1280*720 720*1280, 1280*720 The size of the generated media in pixels (width*height).
- `seed`: integer No - -1 ~ 2147483647 The random seed to use for the generation.

### Minicpm V Video

- **Model ID:** `wavespeed-ai/minicpm-v/video`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/minicpm-v/video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/minicpm-v-video

**Request Parameters**

- `video`: string Yes - Video to be analyzed.
- `preset_prompt`: string No describe describe, caption Preset prompt for image analysis.
- `custom_prompt`: string No - - Custom prompt for image analysis.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Molmo2 Video Captioner

- **Model ID:** `wavespeed-ai/molmo2/video-captioner`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/molmo2/video-captioner`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/molmo2-video-captioner

**Request Parameters**

- `video`: string Yes - Input video URL for captioning. Supports common video formats (MP4, MOV, WebM). Maximum 2 minutes.
- `detail_level`: string No medium low, medium, high Level of detail in the generated caption. Low: brief summary. Medium: balanced description. High: comprehensive, detailed analysis with temporal dynamics.

### Molmo2 Video Content Moderator

- **Model ID:** `wavespeed-ai/molmo2/video-content-moderator`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/molmo2/video-content-moderator`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/molmo2-video-content-moderator

**Request Parameters**

- `video`: string Yes - Video URL to moderate and analyze for safety compliance. Supports MP4, MOV, WebM formats.
- `text`: string No - - Optional text prompt or question about the video content for contextual analysis.

### Molmo2 Video Qa

- **Model ID:** `wavespeed-ai/molmo2/video-qa`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/molmo2/video-qa`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/molmo2-video-qa

**Request Parameters**

- `video`: string Yes - Input video URL for question answering. Supports common video formats (MP4, MOV, WebM). Maximum 2 minutes.
- `text`: string Yes - - Your question about the video content.

### Molmo2 Video Understanding

- **Model ID:** `wavespeed-ai/molmo2/video-understanding`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/molmo2/video-understanding`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/molmo2-video-understanding

**Request Parameters**

- `video`: string Yes - Input video URL for understanding. Supports common video formats (MP4, MOV, WebM). Maximum 2 minutes.
- `task`: string No general general, summary, analysis, counting, scene_description Type of understanding task. General: overall understanding. Summary: brief overview. Analysis: detailed breakdown. Counting: count objects/actions. Scene_description:
- `text`: string No - - Optional guidance or specific instructions for the understanding task (e.g., 'Focus on the people' or 'Count the number of cars').

### Openai Whisper With Video

- **Model ID:** `wavespeed-ai/openai-whisper-with-video`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/openai-whisper-with-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/openai-whisper-with-video

**Request Parameters**

- `video`: string Yes - Video file or URL to transcribe. Provide an HTTPS URL or upload a video file. Audio will be extracted for transcription.
- `language`: string No auto auto, af, am, ar, as, az, ba, be, bg, bn, bo, br, bs, ca, cs, cy, da, de, el, en, es, et, eu, fa, fi, fo, fr, gl, gu, ha, haw, he, hi, hr, ht, hu, hy, id, is, it, ja, jw, ka, kk, km, kn, ko, la, lb, ln, lo, lt, lv, mg, mi, mk
- `task`: string No transcribe transcribe, translate The task to perform. 'transcribe' to the source language or 'translate' to English.
- `enable_timestamps`: boolean No false - Enable to generate word-level timestamps for the transcription. Note: This may increase processing time.
- `prompt`: string No - An optional text to provide as a prompt to guide the model's style or continue a previous audio segment. The prompt should be in the same language as the audio.
- `enable_sync_mode`: boolean No false - If set to true, the function will wait for the result to be generated and uploaded before returning the response. It allows you to get the result directly in the response. This property is only available through the API.

### Sam3 Video

- **Model ID:** `wavespeed-ai/sam3-video`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/sam3-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/sam3-video

**Request Parameters**

- `video`: string Yes - Video URL for segmented
- `prompt`: string Yes - Text prompt for segmentation. Use commas to track multiple objects (e.g., 'person, cloth').
- `apply_mask`: boolean No true - Whether to apply mask to video

### Sam3 Video Rle

- **Model ID:** `wavespeed-ai/sam3-video-rle`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/sam3-video-rle`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/sam3-video-rle

**Request Parameters**

- `video`: string Yes - Video URL for segmented
- `prompt`: string Yes - Text prompt for segmentation. Use commas to track multiple objects (e.g., 'person, cloth').
- `point_prompts`: array No [] - List of point coordinates to mark specific locations for segmentation (foreground or background)
- `box_prompts`: array No [] - List of bounding boxes to define rectangular regions for segmentation
- `apply_mask`: boolean No true - Whether to apply mask to video

### Short Video Generator

- **Model ID:** `wavespeed-ai/short-video-generator`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/short-video-generator`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/short-video-generator

**Request Parameters**

- `prompt`: string Yes - Describe the scene, action, camera movement, and mood for the video.
- `images`: array No [] - Optional reference images (up to 4) to guide the visual style, characters, or scene composition.
- `aspect_ratio`: string No 16:9 16:9, 9:16, 4:3, 3:4 The aspect ratio of the generated video.
- `duration`: integer No 5 5, 10, 15 The duration of the generated video in seconds.

### Tiktok Video Generator

- **Model ID:** `wavespeed-ai/tiktok-video-generator`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/tiktok-video-generator`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/tiktok-video-generator

**Request Parameters**

- `prompt`: string Yes - Describe the scene, action, camera movement, and mood for the video.
- `images`: array No [] - Optional reference images (up to 4) to guide the visual style, characters, or scene composition.
- `aspect_ratio`: string No 16:9 16:9, 9:16, 4:3, 3:4 The aspect ratio of the generated video.
- `duration`: integer No 5 5, 10, 15 The duration of the generated video in seconds.

### Ugc Video Generator

- **Model ID:** `wavespeed-ai/ugc-video-generator`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/ugc-video-generator`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/ugc-video-generator

**Request Parameters**

- `prompt`: string Yes - Describe the scene, action, camera movement, and mood for the video.
- `images`: array No [] - Optional reference images (up to 4) to guide the visual style, characters, or scene composition.
- `aspect_ratio`: string No 16:9 16:9, 9:16, 4:3, 3:4 The aspect ratio of the generated video.
- `duration`: integer No 5 5, 10, 15 The duration of the generated video in seconds.

### Vace Video Joiner

- **Model ID:** `wavespeed-ai/vace-video-joiner`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/vace-video-joiner`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/vace-video-joiner

**Request Parameters**

- `videos`: array Yes - 2 ~ 4 items Array of video URLs to join (minimum 2, maximum 4).

### Video Body Swap

- **Model ID:** `wavespeed-ai/video-body-swap`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/video-body-swap`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/video-body-swap

**Request Parameters**

- `image`: string Yes - The URL of the face/head image.
- `video`: string Yes - The URL of the target body video.

### Video Converter

- **Model ID:** `wavespeed-ai/video-converter`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/video-converter`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/video-converter

**Request Parameters**

- `video`: string Yes - The URL of the input video.
- `output_format`: string Yes - mp4, webm, avi, mov, mkv, gif The target format to convert the video to (mp4, webm, avi, mov, mkv, gif).

### Video Eraser

- **Model ID:** `wavespeed-ai/video-eraser`
- **Operation:** `edit_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/video-eraser`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/video-eraser

**Request Parameters**

- `video`: string Yes - The video for generating the output.
- `prompt`: string No - The text prompt for specifying the objects or areas to be removed from the video.
- `mask_image`: string No - The mask image to indicate the area to be erased. The area to be erased should be in white color and the area to be kept should be in black color.

### Video Face Swap

- **Model ID:** `wavespeed-ai/video-face-swap`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/video-face-swap`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/video-face-swap

**Request Parameters**

- `video`: string Yes - The video that contains the face to be replaced.
- `face_image`: string Yes - - The face image as reference.
- `target_index`: integer No - 0 ~ 10 0 = largest face. To switch to another target face - switch to index 1, e.t.c.

### Video Fps Increaser

- **Model ID:** `wavespeed-ai/video-fps-increaser`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/video-fps-increaser`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/video-fps-increaser

**Request Parameters**

- `video`: string Yes - The URL of the video to increase frame rate.

### Video Head Swap

- **Model ID:** `wavespeed-ai/video-head-swap`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/video-head-swap`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/video-head-swap

**Request Parameters**

- `video`: string Yes - The video that contains the face to be replaced.
- `face_image`: string Yes - - The face image as reference.
- `prompt`: string No - The prompt to guide the model's behavior.
- `resolution`: string No 480p 720p, 480p The resolution of the output video.
- `seed`: integer No -1 -1 ~ 2147483647 The seed used for the prediction.

### Video Watermark Remover

- **Model ID:** `wavespeed-ai/video-watermark-remover`
- **Operation:** `edit_image`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/video-watermark-remover`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/video-watermark-remover

**Request Parameters**

- `video`: string Yes - The video for generating the output.

### Wan 2.1 I2V 480p

- **Model ID:** `wavespeed-ai/wan-2.1/i2v-480p`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/wan-2.1/i2v-480p`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/wan-2.1-i2v-480p

**Request Parameters**

- `image`: string Yes - The image for generating the output.
- `prompt`: string Yes - The positive prompt for the generation.
- `negative_prompt`: string No - The negative prompt for the generation.
- `size`: string No 832*480 832*480, 480*832 The size of the generated media in pixels (width*height).
- `num_inference_steps`: integer No 30 1 ~ 40 The number of inference steps to perform.
- `duration`: integer No 5 5 ~ 10 The duration of the generated media in seconds.
- `guidance_scale`: number No 5 0.00 ~ 20.00 The guidance scale to use for the generation.
- `flow_shift`: number No 3 1.0 ~ 10.0 The shift value for the timestep schedule for flow matching.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Wan 2.1 I2V 480p LoRA

- **Model ID:** `wavespeed-ai/wan-2.1/i2v-480p-lora`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/wan-2.1/i2v-480p-lora`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/wan-2.1-i2v-480p-lora

**Request Parameters**

- `image`: string Yes - The image for generating the output.
- `prompt`: string Yes - The positive prompt for the generation.
- `negative_prompt`: string No - The negative prompt for the generation.
- `loras`: array No max 3 items List of LoRAs to apply (max 3). loras[].path string Yes - Path to the LoRA model loras[].scale float Yes - 0.0 ~ 4.0 Scale of the LoRA model
- `size`: string No 832*480 832*480, 480*832 The size of the generated media in pixels (width*height).
- `num_inference_steps`: integer No 30 1 ~ 40 The number of inference steps to perform.
- `duration`: integer No 5 5, 10 The duration of the generated media in seconds.
- `guidance_scale`: number No 5 0.00 ~ 20.00 The guidance scale to use for the generation.
- `flow_shift`: number No 3 1.0 ~ 10.0 The shift value for the timestep schedule for flow matching.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Wan 2.1 I2V 480p LoRA Ultra Fast

- **Model ID:** `wavespeed-ai/wan-2.1/i2v-480p-lora-ultra-fast`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/wan-2.1/i2v-480p-lora-ultra-fast`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/wan-2.1-i2v-480p-lora-ultra-fast

**Request Parameters**

- `image`: string Yes - The image for generating the output.
- `prompt`: string Yes - The positive prompt for the generation.
- `negative_prompt`: string No - The negative prompt for the generation.
- `loras`: array No max 3 items List of LoRAs to apply (max 3). loras[].path string Yes - Path to the LoRA model loras[].scale float Yes - 0.0 ~ 4.0 Scale of the LoRA model
- `size`: string No 832*480 832*480, 480*832 The size of the generated media in pixels (width*height).
- `num_inference_steps`: integer No 30 1 ~ 40 The number of inference steps to perform.
- `duration`: integer No 5 5 ~ 10 The duration of the generated media in seconds.
- `guidance_scale`: number No 5 0.00 ~ 20.00 The guidance scale to use for the generation.
- `flow_shift`: number No 3 1.0 ~ 10.0 The shift value for the timestep schedule for flow matching.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Wan 2.1 I2V 480p Ultra Fast

- **Model ID:** `wavespeed-ai/wan-2.1/i2v-480p-ultra-fast`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/wan-2.1/i2v-480p-ultra-fast`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/wan-2.1-i2v-480p-ultra-fast

**Request Parameters**

- `image`: string Yes - The image for generating the output.
- `prompt`: string Yes - The positive prompt for the generation.
- `negative_prompt`: string No - The negative prompt for the generation.
- `size`: string No 832*480 832*480, 480*832 The size of the generated media in pixels (width*height).
- `num_inference_steps`: integer No 30 1 ~ 40 The number of inference steps to perform.
- `duration`: integer No 5 5 ~ 10 The duration of the generated media in seconds.
- `guidance_scale`: number No 5 0.00 ~ 20.00 The guidance scale to use for the generation.
- `flow_shift`: number No 3 1.0 ~ 10.0 The shift value for the timestep schedule for flow matching.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Wan 2.1 I2V 720p

- **Model ID:** `wavespeed-ai/wan-2.1/i2v-720p`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/wan-2.1/i2v-720p`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/wan-2.1-i2v-720p

**Request Parameters**

- `image`: string Yes - The image for generating the output.
- `prompt`: string Yes - The positive prompt for the generation.
- `negative_prompt`: string No - The negative prompt for the generation.
- `size`: string No 1280*720 1280*720, 720*1280 The size of the generated media in pixels (width*height).
- `num_inference_steps`: integer No 30 1 ~ 40 The number of inference steps to perform.
- `duration`: integer No 5 5 ~ 10 The duration of the generated media in seconds.
- `guidance_scale`: number No 5 0.00 ~ 20.00 The guidance scale to use for the generation.
- `flow_shift`: number No 5 1.0 ~ 10.0 The shift value for the timestep schedule for flow matching.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Wan 2.1 I2V 720p LoRA

- **Model ID:** `wavespeed-ai/wan-2.1/i2v-720p-lora`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/wan-2.1/i2v-720p-lora`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/wan-2.1-i2v-720p-lora

**Request Parameters**

- `image`: string Yes - The image for generating the output.
- `prompt`: string Yes -
- `negative_prompt`: string No - The negative prompt for the generation.
- `loras`: array No max 3 items The LoRA weights for generating the output. loras[].path string Yes - Path to the LoRA model loras[].scale float Yes - 0.0 ~ 4.0 Scale of the LoRA model
- `size`: string No 1280*720 1280*720, 720*1280 The size of the generated media in pixels (width*height).
- `num_inference_steps`: integer No 30 1 ~ 40 The number of inference steps to perform.
- `duration`: integer No 5 5 ~ 10 The duration of the generated media in seconds.
- `guidance_scale`: number No 5 0.00 ~ 20.00 The guidance scale to use for the generation.
- `flow_shift`: number No 5 1.0 ~ 10.0 The shift value for the timestep schedule for flow matching.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Wan 2.1 I2V 720p LoRA Ultra Fast

- **Model ID:** `wavespeed-ai/wan-2.1/i2v-720p-lora-ultra-fast`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/wan-2.1/i2v-720p-lora-ultra-fast`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/wan-2.1-i2v-720p-lora-ultra-fast

**Request Parameters**

- `image`: string Yes - The image for generating the output.
- `prompt`: string Yes - The positive prompt for the generation.
- `negative_prompt`: string No - The negative prompt for the generation.
- `loras`: array No max 3 items List of LoRAs to apply (max 3). loras[].path string Yes - Path to the LoRA model loras[].scale float Yes - 0.0 ~ 4.0 Scale of the LoRA model
- `size`: string No 1280*720 1280*720, 720*1280 The size of the generated media in pixels (width*height).
- `num_inference_steps`: integer No 30 1 ~ 40 The number of inference steps to perform.
- `duration`: integer No 5 5 ~ 10 The duration of the generated media in seconds.
- `guidance_scale`: number No 5 0.00 ~ 20.00 The guidance scale to use for the generation.
- `flow_shift`: number No 5 1.0 ~ 10.0 The shift value for the timestep schedule for flow matching.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Wan 2.1 I2V 720p Ultra Fast

- **Model ID:** `wavespeed-ai/wan-2.1/i2v-720p-ultra-fast`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/wan-2.1/i2v-720p-ultra-fast`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/wan-2.1-i2v-720p-ultra-fast

**Request Parameters**

- `image`: string Yes - The image for generating the output.
- `prompt`: string Yes - The positive prompt for the generation.
- `negative_prompt`: string No - The negative prompt for the generation.
- `size`: string No 1280*720 1280*720, 720*1280 The size of the generated media in pixels (width*height).
- `num_inference_steps`: integer No 30 1 ~ 40 The number of inference steps to perform.
- `duration`: integer No 5 5 ~ 10 The duration of the generated media in seconds.
- `guidance_scale`: number No 5 0.00 ~ 20.00 The guidance scale to use for the generation.
- `flow_shift`: number No 5 1.0 ~ 10.0 The shift value for the timestep schedule for flow matching.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Wan 2.1 T2V 480p

- **Model ID:** `wavespeed-ai/wan-2.1/t2v-480p`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/wan-2.1/t2v-480p`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/wan-2.1-t2v-480p

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `negative_prompt`: string No - The negative prompt for the generation.
- `size`: string No 832*480 832*480, 480*832 The size of the generated media in pixels (width*height).
- `num_inference_steps`: integer No 30 1 ~ 40 The number of inference steps to perform.
- `duration`: integer No 5 5 ~ 10 The duration of the generated media in seconds.
- `guidance_scale`: number No 5 0.00 ~ 20.00 The guidance scale to use for the generation.
- `flow_shift`: number No 3 1.0 ~ 10.0 The shift value for the timestep schedule for flow matching.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Wan 2.1 T2V 480p LoRA

- **Model ID:** `wavespeed-ai/wan-2.1/t2v-480p-lora`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/wan-2.1/t2v-480p-lora`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/wan-2.1-t2v-480p-lora

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `negative_prompt`: string No - The negative prompt for the generation.
- `loras`: array No max 3 items List of LoRAs to apply (max 3). loras[].path string Yes - Path to the LoRA model loras[].scale float Yes - 0.0 ~ 4.0 Scale of the LoRA model
- `size`: string No 832*480 832*480, 480*832 The size of the generated media in pixels (width*height).
- `num_inference_steps`: integer No 30 1 ~ 40 The number of inference steps to perform.
- `duration`: integer No 5 5 ~ 10 The duration of the generated media in seconds.
- `guidance_scale`: number No 5 0.00 ~ 20.00 The guidance scale to use for the generation.
- `flow_shift`: number No 3 1.0 ~ 10.0 The shift value for the timestep schedule for flow matching.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Wan 2.1 T2V 480p LoRA Ultra Fast

- **Model ID:** `wavespeed-ai/wan-2.1/t2v-480p-lora-ultra-fast`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/wan-2.1/t2v-480p-lora-ultra-fast`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/wan-2.1-t2v-480p-lora-ultra-fast

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `negative_prompt`: string No - The negative prompt for the generation.
- `loras`: array No max 3 items List of LoRAs to apply (max 3). loras[].path string Yes - Path to the LoRA model loras[].scale float Yes - 0.0 ~ 4.0 Scale of the LoRA model
- `size`: string No 832*480 832*480, 480*832 The size of the generated media in pixels (width*height).
- `num_inference_steps`: integer No 30 1 ~ 40 The number of inference steps to perform.
- `duration`: integer No 5 5 ~ 10 The duration of the generated media in seconds.
- `guidance_scale`: number No 5 0.00 ~ 20.00 The guidance scale to use for the generation.
- `flow_shift`: number No 3 1.0 ~ 10.0 The shift value for the timestep schedule for flow matching.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Wan 2.1 T2V 480p Ultra Fast

- **Model ID:** `wavespeed-ai/wan-2.1/t2v-480p-ultra-fast`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/wan-2.1/t2v-480p-ultra-fast`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/wan-2.1-t2v-480p-ultra-fast

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `negative_prompt`: string No - The negative prompt for the generation.
- `size`: string No 832*480 832*480, 480*832 The size of the generated media in pixels (width*height).
- `num_inference_steps`: integer No 30 0 ~ 40 The number of inference steps to perform.
- `duration`: integer No 5 5 ~ 10 The duration of the generated media in seconds.
- `guidance_scale`: number No 5 0.00 ~ 20.00 The guidance scale to use for the generation.
- `flow_shift`: number No 3 1.0 ~ 10.0 The shift value for the timestep schedule for flow matching.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Wan 2.1 T2V 720p

- **Model ID:** `wavespeed-ai/wan-2.1/t2v-720p`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/wan-2.1/t2v-720p`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/wan-2.1-t2v-720p

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `negative_prompt`: string No - The negative prompt for the generation.
- `size`: string No 1280*720 1280*720, 720*1280 The size of the generated media in pixels (width*height).
- `num_inference_steps`: integer No 30 1 ~ 40 The number of inference steps to perform.
- `duration`: integer No 5 5 ~ 10 The duration of the generated media in seconds.
- `guidance_scale`: number No 5 0.00 ~ 20.00 The guidance scale to use for the generation.
- `flow_shift`: number No 5 1.0 ~ 10.0 The shift value for the timestep schedule for flow matching.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Wan 2.1 T2V 720p LoRA

- **Model ID:** `wavespeed-ai/wan-2.1/t2v-720p-lora`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/wan-2.1/t2v-720p-lora`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/wan-2.1-t2v-720p-lora

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `negative_prompt`: string No - The negative prompt for the generation.
- `loras`: array No max 3 items List of LoRAs to apply (max 3). loras[].path string Yes - Path to the LoRA model loras[].scale float Yes - 0.0 ~ 4.0 Scale of the LoRA model
- `size`: string No 1280*720 1280*720, 720*1280 The size of the generated media in pixels (width*height).
- `num_inference_steps`: integer No 30 1 ~ 40 The number of inference steps to perform.
- `duration`: integer No 5 5 ~ 10 The duration of the generated media in seconds.
- `guidance_scale`: number No 5 0.00 ~ 20.00 The guidance scale to use for the generation.
- `flow_shift`: number No 5 1.0 ~ 10.0 The shift value for the timestep schedule for flow matching.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Wan 2.1 T2V 720p LoRA Ultra Fast

- **Model ID:** `wavespeed-ai/wan-2.1/t2v-720p-lora-ultra-fast`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/wan-2.1/t2v-720p-lora-ultra-fast`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/wan-2.1-t2v-720p-lora-ultra-fast

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `negative_prompt`: string No - The negative prompt for the generation.
- `loras`: array No max 3 items List of LoRAs to apply (max 3). loras[].path string Yes - Path to the LoRA model loras[].scale float Yes - 0.0 ~ 4.0 Scale of the LoRA model
- `size`: string No 1280*720 1280*720, 720*1280 The size of the generated media in pixels (width*height).
- `num_inference_steps`: integer No 30 1 ~ 40 The number of inference steps to perform.
- `duration`: integer No 5 5 ~ 10 The duration of the generated media in seconds.
- `guidance_scale`: number No 5 0.00 ~ 20.00 The guidance scale to use for the generation.
- `flow_shift`: number No 5 1.0 ~ 10.0 The shift value for the timestep schedule for flow matching.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Wan 2.1 T2V 720p Ultra Fast

- **Model ID:** `wavespeed-ai/wan-2.1/t2v-720p-ultra-fast`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/wan-2.1/t2v-720p-ultra-fast`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/wan-2.1-t2v-720p-ultra-fast

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `negative_prompt`: string No - The negative prompt for the generation.
- `size`: string No 1280*720 1280*720, 720*1280 The size of the generated media in pixels (width*height).
- `num_inference_steps`: integer No 30 0 ~ 40 The number of inference steps to perform.
- `duration`: integer No 5 5 ~ 10 The duration of the generated media in seconds.
- `guidance_scale`: number No 5 0.00 ~ 20.00 The guidance scale to use for the generation.
- `flow_shift`: number No 5 1.0 ~ 10.0 The shift value for the timestep schedule for flow matching.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Wan 2.1 V2V 480p

- **Model ID:** `wavespeed-ai/wan-2.1/v2v-480p`
- **Operation:** `video_edit`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/wan-2.1/v2v-480p`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/wan-2.1-v2v-480p

**Request Parameters**

- `video`: string Yes - The video for generating the output.
- `prompt`: string Yes -
- `negative_prompt`: string No - The negative prompt for the generation.
- `num_inference_steps`: integer No 30 1 ~ 40 The number of inference steps to perform.
- `duration`: integer No 5 5 ~ 10 The duration of the generated media in seconds.
- `strength`: number No 0.9 0.10 ~ 1.00
- `guidance_scale`: number No 5 0.00 ~ 20.00 The guidance scale to use for the generation.
- `flow_shift`: number No 3 1.0 ~ 10.0 The shift value for the timestep schedule for flow matching.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Wan 2.1 V2V 480p LoRA

- **Model ID:** `wavespeed-ai/wan-2.1/v2v-480p-lora`
- **Operation:** `video_edit`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/wan-2.1/v2v-480p-lora`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/wan-2.1-v2v-480p-lora

**Request Parameters**

- `video`: string Yes - The video for generating the output.
- `prompt`: string Yes -
- `loras`: array No max 3 items List of LoRAs to apply (max 3). loras[].path string Yes - Path to the LoRA model loras[].scale float Yes - 0.0 ~ 4.0 Scale of the LoRA model
- `negative_prompt`: string No - The negative prompt for the generation.
- `num_inference_steps`: integer No 30 1 ~ 40 The number of inference steps to perform.
- `duration`: integer No 5 5 ~ 10 The duration of the generated media in seconds.
- `strength`: number No 0.9 0.10 ~ 1.00
- `guidance_scale`: number No 5 0.00 ~ 20.00 The guidance scale to use for the generation.
- `flow_shift`: number No 3 1.0 ~ 10.0 The shift value for the timestep schedule for flow matching.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Wan 2.1 V2V 480p LoRA Ultra Fast

- **Model ID:** `wavespeed-ai/wan-2.1/v2v-480p-lora-ultra-fast`
- **Operation:** `video_edit`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/wan-2.1/v2v-480p-lora-ultra-fast`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/wan-2.1-v2v-480p-lora-ultra-fast

**Request Parameters**

- `video`: string Yes - The video for generating the output.
- `prompt`: string Yes -
- `loras`: array No max 3 items List of LoRAs to apply (max 3). loras[].path string Yes - Path to the LoRA model loras[].scale float Yes - 0.0 ~ 4.0 Scale of the LoRA model
- `negative_prompt`: string No - The negative prompt for the generation.
- `num_inference_steps`: integer No 30 1 ~ 40 The number of inference steps to perform.
- `duration`: integer No 5 5 ~ 10 The duration of the generated media in seconds.
- `strength`: number No 0.9 0.10 ~ 1.00
- `guidance_scale`: number No 5 0.00 ~ 20.00 The guidance scale to use for the generation.
- `flow_shift`: number No 3 1.0 ~ 10.0 The shift value for the timestep schedule for flow matching.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Wan 2.1 V2V 480p Ultra Fast

- **Model ID:** `wavespeed-ai/wan-2.1/v2v-480p-ultra-fast`
- **Operation:** `video_edit`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/wan-2.1/v2v-480p-ultra-fast`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/wan-2.1-v2v-480p-ultra-fast

**Request Parameters**

- `video`: string Yes - The video for generating the output.
- `prompt`: string Yes -
- `negative_prompt`: string No - The negative prompt for the generation.
- `num_inference_steps`: integer No 30 1 ~ 40 The number of inference steps to perform.
- `duration`: integer No 5 5 ~ 10 The duration of the generated media in seconds.
- `strength`: number No 0.9 0.10 ~ 1.00
- `guidance_scale`: number No 5 0.00 ~ 20.00 The guidance scale to use for the generation.
- `flow_shift`: number No 3 1.0 ~ 10.0 The shift value for the timestep schedule for flow matching.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Wan 2.1 V2V 720p

- **Model ID:** `wavespeed-ai/wan-2.1/v2v-720p`
- **Operation:** `video_edit`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/wan-2.1/v2v-720p`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/wan-2.1-v2v-720p

**Request Parameters**

- `video`: string Yes - The video for generating the output.
- `prompt`: string Yes -
- `negative_prompt`: string No - The negative prompt for the generation.
- `num_inference_steps`: integer No 30 1 ~ 40 The number of inference steps to perform.
- `duration`: integer No 5 5 ~ 10 The duration of the generated media in seconds.
- `strength`: number No 0.9 0.10 ~ 1.00
- `guidance_scale`: number No 5 0.00 ~ 20.00 The guidance scale to use for the generation.
- `flow_shift`: number No 3 1.0 ~ 10.0 The shift value for the timestep schedule for flow matching.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Wan 2.1 V2V 720p LoRA

- **Model ID:** `wavespeed-ai/wan-2.1/v2v-720p-lora`
- **Operation:** `video_edit`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/wan-2.1/v2v-720p-lora`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/wan-2.1-v2v-720p-lora

**Request Parameters**

- `video`: string Yes - The video for generating the output.
- `prompt`: string Yes -
- `loras`: array No max 3 items List of LoRAs to apply (max 3). loras[].path string Yes - Path to the LoRA model loras[].scale float Yes - 0.0 ~ 4.0 Scale of the LoRA model
- `negative_prompt`: string No - The negative prompt for the generation.
- `num_inference_steps`: integer No 30 1 ~ 40 The number of inference steps to perform.
- `duration`: integer No 5 5 ~ 10 The duration of the generated media in seconds.
- `strength`: number No 0.9 0.10 ~ 1.00
- `guidance_scale`: number No 5 0.00 ~ 20.00 The guidance scale to use for the generation.
- `flow_shift`: number No 3 1.0 ~ 10.0 The shift value for the timestep schedule for flow matching.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Wan 2.1 V2V 720p LoRA Ultra Fast

- **Model ID:** `wavespeed-ai/wan-2.1/v2v-720p-lora-ultra-fast`
- **Operation:** `video_edit`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/wan-2.1/v2v-720p-lora-ultra-fast`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/wan-2.1-v2v-720p-lora-ultra-fast

**Request Parameters**

- `video`: string Yes - The video for generating the output.
- `prompt`: string Yes -
- `loras`: array No max 3 items List of LoRAs to apply (max 3). loras[].path string Yes - Path to the LoRA model loras[].scale float Yes - 0.0 ~ 4.0 Scale of the LoRA model
- `negative_prompt`: string No - The negative prompt for the generation.
- `num_inference_steps`: integer No 30 1 ~ 40 The number of inference steps to perform.
- `duration`: integer No 5 5 ~ 10 The duration of the generated media in seconds.
- `strength`: number No 0.9 0.10 ~ 1.00
- `guidance_scale`: number No 5 0.00 ~ 20.00 The guidance scale to use for the generation.
- `flow_shift`: number No 3 1.0 ~ 10.0 The shift value for the timestep schedule for flow matching.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Wan 2.1 V2V 720p Ultra Fast

- **Model ID:** `wavespeed-ai/wan-2.1/v2v-720p-ultra-fast`
- **Operation:** `video_edit`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/wan-2.1/v2v-720p-ultra-fast`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/wan-2.1-v2v-720p-ultra-fast

**Request Parameters**

- `video`: string Yes - The video for generating the output.
- `prompt`: string Yes -
- `negative_prompt`: string No - The negative prompt for the generation.
- `num_inference_steps`: integer No 30 1 ~ 40 The number of inference steps to perform.
- `duration`: integer No 5 5 ~ 10 The duration of the generated media in seconds.
- `strength`: number No 0.9 0.10 ~ 1.00
- `guidance_scale`: number No 5 0.00 ~ 20.00 The guidance scale to use for the generation.
- `flow_shift`: number No 3 1.0 ~ 10.0 The shift value for the timestep schedule for flow matching.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Wan 2.2 I2V LoRA Trainer

- **Model ID:** `wavespeed-ai/wan-2.2-i2v-lora-trainer`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/wan-2.2-i2v-lora-trainer`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/wan-2.2-i2v-lora-trainer

**Request Parameters**

- `data`: string Yes - - To train a WAN I2V LoRA, you need to upload a zip file containing videos. In addition to videos the archive can contain text files with captions. Each text file should have the same name as the video file it corresponds to.
- `trigger_word`: string No p3r5on - The phrase that will trigger the model to generate an video.
- `steps`: integer No 100 50 ~ 1500 Number of steps to train the LoRA on.
- `learning_rate`: number No 0.0002 0.00000 ~ 1.00000
- `lora_rank`: integer No 32 1 ~ 128

### Wan 2.2 Spicy Image To Video

- **Model ID:** `wavespeed-ai/wan-2.2-spicy/image-to-video`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/wan-2.2-spicy/image-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/wan-2.2-spicy-image-to-video

**Request Parameters**

- `image`: string Yes - The image for generating the output.
- `prompt`: string Yes - The positive prompt for the generation.
- `resolution`: string No 480p 480p, 720p The resolution of the generated media.
- `duration`: integer No 5 5, 8 The duration of the generated media in seconds.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Wan 2.2 Spicy Image To Video LoRA

- **Model ID:** `wavespeed-ai/wan-2.2-spicy/image-to-video-lora`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/wan-2.2-spicy/image-to-video-lora`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/wan-2.2-spicy-image-to-video-lora

**Request Parameters**

- `image`: string Yes - The image for generating the output.
- `prompt`: string Yes - The positive prompt for the generation.
- `resolution`: string No 480p 480p, 720p The resolution of the generated media.
- `duration`: integer No 5 5, 8 The duration of the generated media in seconds.
- `loras`: array No max 3 items List of LoRAs to apply (max 3). loras[].path string Yes - Path to the LoRA model loras[].scale float Yes - 0.0 ~ 4.0 Scale of the LoRA model
- `high_noise_loras`: array No - - List of high noise LoRAs to apply (max 3).
- `low_noise_loras`: array No - - List of low noise LoRAs to apply (max 3).
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Wan 2.2 Spicy Video Extend

- **Model ID:** `wavespeed-ai/wan-2.2-spicy/video-extend`
- **Operation:** `video_extend`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/wan-2.2-spicy/video-extend`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/wan-2.2-spicy-video-extend

**Request Parameters**

- `video`: string Yes - The video for generating the output.
- `prompt`: string Yes - The positive prompt for the generation.
- `resolution`: string No 480p 480p, 720p The resolution of the generated media.
- `duration`: integer No 5 5, 8 The duration of the generated media in seconds.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Wan 2.2 Spicy Video Extend LoRA

- **Model ID:** `wavespeed-ai/wan-2.2-spicy/video-extend-lora`
- **Operation:** `video_extend`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/wan-2.2-spicy/video-extend-lora`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/wan-2.2-spicy-video-extend-lora

**Request Parameters**

- `video`: string Yes - The video for generating the output.
- `prompt`: string Yes - The positive prompt for the generation.
- `resolution`: string No 480p 480p, 720p The resolution of the generated media.
- `duration`: integer No 5 5, 8 The duration of the generated media in seconds.
- `loras`: array No max 3 items List of LoRAs to apply (max 3). loras[].path string Yes - Path to the LoRA model loras[].scale float Yes - 0.0 ~ 4.0 Scale of the LoRA model
- `high_noise_loras`: array No - - List of high noise LoRAs to apply (max 3).
- `low_noise_loras`: array No - - List of low noise LoRAs to apply (max 3).
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Wan 2.2 I2V 480p

- **Model ID:** `wavespeed-ai/wan-2.2/i2v-480p`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/wan-2.2/i2v-480p`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/wan-2.2-i2v-480p

**Request Parameters**

- `image`: string Yes - The image for generating the output.
- `prompt`: string Yes - The positive prompt for the generation.
- `negative_prompt`: string No - The negative prompt for the generation.
- `last_image`: string No - - The last image for generating the output.
- `duration`: integer No 5 5, 8 The duration of the generated media in seconds.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Wan 2.2 I2V 480p LoRA

- **Model ID:** `wavespeed-ai/wan-2.2/i2v-480p-lora`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/wan-2.2/i2v-480p-lora`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/wan-2.2-i2v-480p-lora

**Request Parameters**

- `image`: string Yes - The image for generating the output.
- `prompt`: string Yes - The positive prompt for the generation.
- `negative_prompt`: string No - The negative prompt for the generation.
- `last_image`: string No - - The last image for generating the output.
- `duration`: integer No 5 5, 8 The duration of the generated media in seconds.
- `loras`: array No max 3 items List of LoRAs to apply (max 3). loras[].path string Yes - Path to the LoRA model loras[].scale float Yes - 0.0 ~ 4.0 Scale of the LoRA model
- `high_noise_loras`: array No - - List of high noise LoRAs to apply (max 3).
- `low_noise_loras`: array No - - List of low noise LoRAs to apply (max 3).
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Wan 2.2 I2V 480p LoRA Ultra Fast

- **Model ID:** `wavespeed-ai/wan-2.2/i2v-480p-lora-ultra-fast`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/wan-2.2/i2v-480p-lora-ultra-fast`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/wan-2.2-i2v-480p-lora-ultra-fast

**Request Parameters**

- `image`: string Yes - The image for generating the output.
- `prompt`: string Yes - The positive prompt for the generation.
- `negative_prompt`: string No - The negative prompt for the generation.
- `last_image`: string No - - The last image for generating the output.
- `duration`: integer No 5 5, 8 The duration of the generated media in seconds.
- `loras`: array No max 3 items List of LoRAs to apply (max 3). loras[].path string Yes - Path to the LoRA model loras[].scale float Yes - 0.0 ~ 4.0 Scale of the LoRA model
- `high_noise_loras`: array No - - List of high noise LoRAs to apply (max 3).
- `low_noise_loras`: array No - - List of low noise LoRAs to apply (max 3).
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Wan 2.2 I2V 480p Ultra Fast

- **Model ID:** `wavespeed-ai/wan-2.2/i2v-480p-ultra-fast`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/wan-2.2/i2v-480p-ultra-fast`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/wan-2.2-i2v-480p-ultra-fast

**Request Parameters**

- `image`: string Yes - The image for generating the output.
- `last_image`: string No - - The last image for generating the output.
- `prompt`: string Yes - The positive prompt for the generation.
- `negative_prompt`: string No - The negative prompt for the generation.
- `duration`: integer No 5 5, 8 The duration of the generated media in seconds.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Wan 2.2 I2V 5b 720p

- **Model ID:** `wavespeed-ai/wan-2.2/i2v-5b-720p`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/wan-2.2/i2v-5b-720p`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/wan-2.2-i2v-5b-720p

**Request Parameters**

- `image`: string Yes - The image for generating the output.
- `prompt`: string Yes - The positive prompt for the generation.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Wan 2.2 I2V 5b 720p LoRA

- **Model ID:** `wavespeed-ai/wan-2.2/i2v-5b-720p-lora`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/wan-2.2/i2v-5b-720p-lora`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/wan-2.2-i2v-5b-720p-lora

**Request Parameters**

- `image`: string Yes - The image for generating the output.
- `prompt`: string Yes - The positive prompt for the generation.
- `loras`: array No max 3 items List of LoRAs to apply (max 3). loras[].path string Yes - Path to the LoRA model loras[].scale float Yes - 0.0 ~ 4.0 Scale of the LoRA model
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Wan 2.2 I2V 720p

- **Model ID:** `wavespeed-ai/wan-2.2/i2v-720p`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/wan-2.2/i2v-720p`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/wan-2.2-i2v-720p

**Request Parameters**

- `image`: string Yes - The image for generating the output.
- `prompt`: string Yes - The positive prompt for the generation.
- `negative_prompt`: string No - The negative prompt for the generation.
- `last_image`: string No - - The last image for generating the output.
- `duration`: integer No 5 5, 8 The duration of the generated media in seconds.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Wan 2.2 I2V 720p LoRA

- **Model ID:** `wavespeed-ai/wan-2.2/i2v-720p-lora`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/wan-2.2/i2v-720p-lora`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/wan-2.2-i2v-720p-lora

**Request Parameters**

- `image`: string Yes - The image for generating the output.
- `prompt`: string Yes - The positive prompt for the generation.
- `negative_prompt`: string No - The negative prompt for the generation.
- `last_image`: string No - - The last image for generating the output.
- `duration`: integer No 5 5, 8 The duration of the generated media in seconds.
- `loras`: array No max 3 items List of LoRAs to apply (max 3). loras[].path string Yes - Path to the LoRA model loras[].scale float Yes - 0.0 ~ 4.0 Scale of the LoRA model
- `high_noise_loras`: array No - - List of high noise LoRAs to apply (max 3).
- `low_noise_loras`: array No - - List of low noise LoRAs to apply (max 3).
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Wan 2.2 I2V 720p LoRA Ultra Fast

- **Model ID:** `wavespeed-ai/wan-2.2/i2v-720p-lora-ultra-fast`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/wan-2.2/i2v-720p-lora-ultra-fast`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/wan-2.2-i2v-720p-lora-ultra-fast

**Request Parameters**

- `image`: string Yes - The image for generating the output.
- `prompt`: string Yes -
- `negative_prompt`: string No - The negative prompt for the generation.
- `last_image`: string No - - The last image for generating the output.
- `duration`: integer No 5 5, 8 The duration of the generated media in seconds.
- `loras`: array No max 3 items List of LoRAs to apply (max 3). loras[].path string Yes - Path to the LoRA model loras[].scale float Yes - 0.0 ~ 4.0 Scale of the LoRA model
- `high_noise_loras`: array No - - List of high noise LoRAs to apply (max 3).
- `low_noise_loras`: array No - - List of low noise LoRAs to apply (max 3).
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Wan 2.2 I2V 720p Ultra Fast

- **Model ID:** `wavespeed-ai/wan-2.2/i2v-720p-ultra-fast`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/wan-2.2/i2v-720p-ultra-fast`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/wan-2.2-i2v-720p-ultra-fast

**Request Parameters**

- `image`: string Yes - The image for generating the output.
- `prompt`: string Yes - The positive prompt for the generation.
- `negative_prompt`: string No - The negative prompt for the generation.
- `last_image`: string No - - The last image for generating the output.
- `duration`: integer No 5 5, 8 The duration of the generated media in seconds.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Wan 2.2 Image To Video

- **Model ID:** `wavespeed-ai/wan-2.2/image-to-video`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/wan-2.2/image-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/wan-2.2-image-to-video

**Request Parameters**

- `image`: string Yes - The image for generating the output.
- `prompt`: string Yes - The positive prompt for the generation.
- `resolution`: string No 480p 480p, 720p The resolution of the generated media.
- `negative_prompt`: string No - The negative prompt for the generation.
- `last_image`: string No - - The last image for generating the output.
- `duration`: integer No 5 5, 8 The duration of the generated media in seconds.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Wan 2.2 Image To Video LoRA

- **Model ID:** `wavespeed-ai/wan-2.2/image-to-video-lora`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/wan-2.2/image-to-video-lora`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/wan-2.2-image-to-video-lora

**Request Parameters**

- `image`: string Yes - The image for generating the output.
- `prompt`: string Yes - The positive prompt for the generation.
- `resolution`: string No 480p 480p, 720p The resolution of the generated media.
- `negative_prompt`: string No - The negative prompt for the generation.
- `last_image`: string No - - The last image for generating the output.
- `duration`: integer No 5 5, 8 The duration of the generated media in seconds.
- `loras`: array No max 3 items List of LoRAs to apply (max 3). loras[].path string Yes - Path to the LoRA model loras[].scale float Yes - 0.0 ~ 4.0 Scale of the LoRA model
- `high_noise_loras`: array No - - List of high noise LoRAs to apply (max 3).
- `low_noise_loras`: array No - - List of low noise LoRAs to apply (max 3).
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Wan 2.2 T2V 480p

- **Model ID:** `wavespeed-ai/wan-2.2/t2v-480p`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/wan-2.2/t2v-480p`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/wan-2.2-t2v-480p

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `negative_prompt`: string No - The negative prompt for the generation.
- `size`: string No 832*480 832*480, 480*832 The size of the generated media in pixels (width*height).
- `duration`: integer No 5 5, 8 The duration of the generated media in seconds.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Wan 2.2 T2V 480p LoRA

- **Model ID:** `wavespeed-ai/wan-2.2/t2v-480p-lora`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/wan-2.2/t2v-480p-lora`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/wan-2.2-t2v-480p-lora

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `negative_prompt`: string No - The negative prompt for the generation.
- `size`: string No 832*480 832*480, 480*832 The size of the generated media in pixels (width*height).
- `duration`: integer No 5 5, 8 The duration of the generated media in seconds.
- `loras`: array No max 3 items List of LoRAs to apply (max 3). loras[].path string Yes - Path to the LoRA model loras[].scale float Yes - 0.0 ~ 4.0 Scale of the LoRA model
- `high_noise_loras`: array No - - List of high noise LoRAs to apply (max 3).
- `low_noise_loras`: array No - - List of low noise LoRAs to apply (max 3).
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Wan 2.2 T2V 480p LoRA Ultra Fast

- **Model ID:** `wavespeed-ai/wan-2.2/t2v-480p-lora-ultra-fast`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/wan-2.2/t2v-480p-lora-ultra-fast`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/wan-2.2-t2v-480p-lora-ultra-fast

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `negative_prompt`: string No - The negative prompt for the generation.
- `size`: string No 832*480 832*480, 480*832 The size of the generated media in pixels (width*height).
- `duration`: integer No 5 5, 8 The duration of the generated media in seconds.
- `loras`: array No max 3 items List of LoRAs to apply (max 3). loras[].path string Yes - Path to the LoRA model loras[].scale float Yes - 0.0 ~ 4.0 Scale of the LoRA model
- `high_noise_loras`: array No - - List of high noise LoRAs to apply (max 3).
- `low_noise_loras`: array No - - List of low noise LoRAs to apply (max 3).
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Wan 2.2 T2V 480p Ultra Fast

- **Model ID:** `wavespeed-ai/wan-2.2/t2v-480p-ultra-fast`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/wan-2.2/t2v-480p-ultra-fast`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/wan-2.2-t2v-480p-ultra-fast

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `negative_prompt`: string No - The negative prompt for the generation.
- `size`: string No 832*480 832*480, 480*832 The size of the generated media in pixels (width*height).
- `duration`: integer No 5 5, 8 The duration of the generated media in seconds.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Wan 2.2 T2V 5b 720p

- **Model ID:** `wavespeed-ai/wan-2.2/t2v-5b-720p`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/wan-2.2/t2v-5b-720p`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/wan-2.2-t2v-5b-720p

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 1280*720 1280*720, 720*1280 The size of the generated media in pixels (width*height).
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Wan 2.2 T2V 5b 720p LoRA

- **Model ID:** `wavespeed-ai/wan-2.2/t2v-5b-720p-lora`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/wan-2.2/t2v-5b-720p-lora`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/wan-2.2-t2v-5b-720p-lora

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `size`: string No 1280*720 1280*720, 720*1280 The size of the generated media in pixels (width*height).
- `loras`: array No max 3 items List of LoRAs to apply (max 3). loras[].path string Yes - Path to the LoRA model loras[].scale float Yes - 0.0 ~ 4.0 Scale of the LoRA model
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Wan 2.2 T2V 720p

- **Model ID:** `wavespeed-ai/wan-2.2/t2v-720p`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/wan-2.2/t2v-720p`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/wan-2.2-t2v-720p

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `negative_prompt`: string No - The negative prompt for the generation.
- `size`: string No 1280*720 1280*720, 720*1280 The size of the generated media in pixels (width*height).
- `duration`: integer No 5 5, 8 The duration of the generated media in seconds.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Wan 2.2 T2V 720p LoRA

- **Model ID:** `wavespeed-ai/wan-2.2/t2v-720p-lora`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/wan-2.2/t2v-720p-lora`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/wan-2.2-t2v-720p-lora

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `negative_prompt`: string No - The negative prompt for the generation.
- `size`: string No 1280*720 1280*720, 720*1280 The size of the generated media in pixels (width*height).
- `duration`: integer No 5 5, 8 The duration of the generated media in seconds.
- `loras`: array No max 3 items List of LoRAs to apply (max 3). loras[].path string Yes - Path to the LoRA model loras[].scale float Yes - 0.0 ~ 4.0 Scale of the LoRA model
- `high_noise_loras`: array No - - List of high noise LoRAs to apply (max 3).
- `low_noise_loras`: array No - - List of low noise LoRAs to apply (max 3).
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Wan 2.2 T2V 720p LoRA Ultra Fast

- **Model ID:** `wavespeed-ai/wan-2.2/t2v-720p-lora-ultra-fast`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/wan-2.2/t2v-720p-lora-ultra-fast`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/wan-2.2-t2v-720p-lora-ultra-fast

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `negative_prompt`: string No - The negative prompt for the generation.
- `size`: string No 1280*720 1280*720, 720*1280 The size of the generated media in pixels (width*height).
- `duration`: integer No 5 5, 8 The duration of the generated media in seconds.
- `loras`: array No max 3 items List of LoRAs to apply (max 3). loras[].path string Yes - Path to the LoRA model loras[].scale float Yes - 0.0 ~ 4.0 Scale of the LoRA model
- `high_noise_loras`: array No - - List of high noise LoRAs to apply (max 3).
- `low_noise_loras`: array No - - List of low noise LoRAs to apply (max 3).
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Wan 2.2 T2V 720p Ultra Fast

- **Model ID:** `wavespeed-ai/wan-2.2/t2v-720p-ultra-fast`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/wan-2.2/t2v-720p-ultra-fast`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/wan-2.2-t2v-720p-ultra-fast

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `negative_prompt`: string No - The negative prompt for the generation.
- `size`: string No 1280*720 1280*720, 720*1280 The size of the generated media in pixels (width*height).
- `duration`: integer No 5 5, 8 The duration of the generated media in seconds.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Wan 2.2 Video Edit

- **Model ID:** `wavespeed-ai/wan-2.2/video-edit`
- **Operation:** `video_edit`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/wan-2.2/video-edit`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/wan-2.2-video-edit

**Request Parameters**

- `video`: string Yes - The video for generating the output.
- `prompt`: string Yes - The positive prompt for the generation.
- `resolution`: string No 480p 480p, 720p The resolution of the output video.
- `seed`: integer No -1 -1 ~ 2147483647 The random seed to use for the generation. -1 means a random seed will be used.

### Video Effects Kissing Pro

- **Model ID:** `wavespeed/kissing-pro`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed/kissing-pro`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/video-effects/video-effects-kissing-pro

**Request Parameters**

- `image`: string Yes - An image to be used as the start frame of the generated video. Requirements: 1. Only accepts 1 image; 2. Only supports dual-person group photos and dual-person collages; 3. Better results are achieved with clear, half-body fron

### X AI Grok Imagine Video Edit Video

- **Model ID:** `x-ai/grok-imagine-video/edit-video`
- **Operation:** `video_edit`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/x-ai/grok-imagine-video/edit-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/x-ai/x-ai-grok-imagine-video-edit-video

**Request Parameters**

- `prompt`: string Yes - Text description of the desired edit.
- `video`: string Yes - URL of the input video to edit. Video will be resized to max 854x480 pixels and truncated to 8 seconds.
- `resolution`: string No 480p 480p, 720p Resolution of the output video.

### X AI Grok Imagine Video Image To Video

- **Model ID:** `x-ai/grok-imagine-video/image-to-video`
- **Operation:** `image_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/x-ai/grok-imagine-video/image-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/x-ai/x-ai-grok-imagine-video-image-to-video

**Request Parameters**

- `image`: string Yes - URL of the input image for video generation.
- `prompt`: string Yes - Text description of desired motion or changes in the video.
- `duration`: integer No 6 6, 10 Video duration in seconds.
- `resolution`: string No 720p 720p, 480p Resolution of the output video.

### X AI Grok Imagine Video Reference To Video

- **Model ID:** `x-ai/grok-imagine-video/reference-to-video`
- **Operation:** `reference_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/x-ai/grok-imagine-video/reference-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/x-ai/x-ai-grok-imagine-video-reference-to-video

**Request Parameters**

- `images`: array Yes [] 1 ~ 7 items Array of reference image URLs for video generation. Up to 7 images supported.
- `prompt`: string Yes - Text description of desired motion or changes in the video.
- `duration`: integer No 6 6, 10 Video duration in seconds.
- `resolution`: string No 720p 720p, 480p Resolution of the output video.

### X AI Grok Imagine Video Text To Video

- **Model ID:** `x-ai/grok-imagine-video/text-to-video`
- **Operation:** `text_to_video`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/x-ai/grok-imagine-video/text-to-video`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/x-ai/x-ai-grok-imagine-video-text-to-video

**Request Parameters**

- `prompt`: string Yes - Text description of the desired video.
- `duration`: integer No 6 6, 10 Video duration in seconds.
- `aspect_ratio`: string No 16:9 16:9, 1:1, 9:16 Aspect ratio of the generated video.
- `resolution`: string No 720p 720p, 480p Resolution of the output video.

### X AI Grok Imagine Video Video Extend

- **Model ID:** `x-ai/grok-imagine-video/video-extend`
- **Operation:** `video_extend`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/x-ai/grok-imagine-video/video-extend`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/x-ai/x-ai-grok-imagine-video-video-extend

**Request Parameters**

- `video`: string Yes - URL of the input video to extend.
- `prompt`: string Yes - Text description of how the video should continue.
- `duration`: integer No 6 6, 10 Duration of the extension in seconds.


## Category: voice_clone

### Minimax Voice Clone

- **Model ID:** `minimax/voice-clone`
- **Operation:** `voice_clone`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/minimax/voice-clone`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/minimax/minimax-voice-clone

**Request Parameters**

- `audio`: string Yes - - The uploaded file is cloned and supports formats such as MP3, M4A, and WAV.
- `custom_voice_id`: string Yes - - Custom user-defined ID. Minimum 8 characters; must include letters and numbers and start with a letter (e.g., WaveSpeed001). Duplicate voice-ids will throw an error.
- `model`: string Yes speech-02-hd speech-02-hd, speech-02-turbo, speech-2.5-hd-preview, speech-2.5-turbo-preview, speech-2.6-hd, speech-2.6-turbo, speech-2.8-hd, speech-2.8-turbo Specify the TTS model to be used for the preview. This is only a previe
- `need_noise_reduction`: boolean No false - Enable noise reduction. Default is false (no noise reduction).
- `need_volume_normalization`: boolean No false - Specify whether to enable volume normalization. If not provided, the default value is false.
- `accuracy`: number No 0.7 0.00 ~ 1.00 Uploading this parameter will set the text validation accuracy threshold, with a value range of [0,1]. If not provided, the default value for this parameter is 0.7.
- `text`: string No Hello! Welcome to Wavespeed! This is a preview of your cloned voice. I hope you enjoy it! - Text for audio preview. Limited to 2000 characters.
- `language_boost`: string No - Chinese, Chinese,Yue, English, Arabic, Russian, Spanish, French, Portuguese, German, Turkish, Dutch, Ukrainian, Vietnamese, Indonesian, Japanese, Italian, Korean, Thai, Polish, Romanian, Greek, Czech, Finnish, Hindi, auto Enhanc

### Minimax Voice Design

- **Model ID:** `minimax/voice-design`
- **Operation:** `voice_design`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/minimax/voice-design`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/minimax/minimax-voice-design

**Request Parameters**

- `prompt`: string Yes - The positive prompt for the generation.
- `custom_voice_id`: string Yes - - Custom user-defined ID. Minimum 8 characters; must include letters and numbers and start with a letter (e.g., WaveSpeed001). Duplicate voice-ids will throw an error.
- `text`: string Yes Hello! Welcome to Wavespeed! This is a preview of your cloned voice. I hope you enjoy it - Text for audio preview. Limited to 500 characters.

### Omnivoice Voice Clone

- **Model ID:** `wavespeed-ai/omnivoice/voice-clone`
- **Operation:** `voice_clone`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/omnivoice/voice-clone`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/omnivoice-voice-clone

**Request Parameters**

- `text`: string Yes - - The text content to convert into speech using the cloned voice.
- `audio`: string Yes - - URL of the reference audio to clone the voice from (3-10 seconds recommended).
- `reference_text`: string No - - Transcript of the reference audio (optional, improves accuracy).
- `speed`: number No 1 0 ~ 5 Playback speed factor. 1.0 = normal speed. Values > 1.0 are faster, < 1.0 are slower.

### Qwen3 Tts Voice Clone

- **Model ID:** `wavespeed-ai/qwen3-tts/voice-clone`
- **Operation:** `voice_clone`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/qwen3-tts/voice-clone`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/qwen3-tts-voice-clone

**Request Parameters**

- `audio`: string Yes - - URL of the reference audio to clone the voice from
- `reference_text`: string No - - Transcript of the reference audio (optional, improves accuracy)
- `text`: string Yes - - The text content to convert into speech using the cloned voice
- `language`: string No auto auto, Chinese, English, German, Italian, Portuguese, Spanish, Japanese, Korean, French, Russian Language of the speech output (use 'auto' for automatic detection)

### Qwen3 Tts Voice Design

- **Model ID:** `wavespeed-ai/qwen3-tts/voice-design`
- **Operation:** `voice_design`
- **Endpoint:** `POST https://api.wavespeed.ai/api/v3/wavespeed-ai/qwen3-tts/voice-design`
- **Result:** `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result`
- **Docs:** https://wavespeed.ai/docs/docs-api/wavespeed-ai/qwen3-tts-voice-design

**Request Parameters**

- `text`: string Yes - - The text content to convert into speech
- `voice_description`: string Yes - - Natural language description of the desired voice characteristics (e.g., 'a warm female voice with a gentle tone')
- `language`: string No auto auto, Chinese, English, German, Italian, Portuguese, Spanish, Japanese, Korean, French, Russian Language of the speech output (use 'auto' for automatic detection)
