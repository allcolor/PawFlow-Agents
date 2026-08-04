#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  printf 'Usage: %s OUTPUT_UI_DIRECTORY\n' "$0" >&2
  exit 2
fi

output_dir=$(realpath -m "$1")
script_dir=$(cd "$(dirname "$0")" && pwd)
recipe_dir="$script_dir/avatar_vendor"
work_dir=$(mktemp -d)
trap 'rm -rf "$work_dir"' EXIT

clone_at() {
  local url=$1
  local revision=$2
  local destination=$3
  git init -q "$destination"
  git -C "$destination" remote add origin "$url"
  git -C "$destination" fetch -q --depth 1 origin "$revision"
  git -C "$destination" checkout -q --detach FETCH_HEAD
}

clone_at https://github.com/met4citizen/TalkingHead.git 67a210b91486a42e58d38fd5682fbfc6754f67bd "$work_dir/TalkingHead"
clone_at https://github.com/met4citizen/HeadAudio.git d3af5f9ff86ab6b2b1913d411a4e1922ec101953 "$work_dir/HeadAudio"
clone_at https://github.com/lhupyn/motion-engine.git bd780a19e10d1cc5736a77946b04e08d658d5bf8 "$work_dir/MotionEngine"

git -C "$work_dir/TalkingHead" apply "$recipe_dir/talkinghead-pawflow.patch"
git -C "$work_dir/MotionEngine" apply "$recipe_dir/motionengine-no-facemirror.patch"

mkdir -p "$work_dir/build"
cp "$recipe_dir/entry.js" "$work_dir/build/entry.js"
(
  cd "$work_dir"
  npm install --no-audit --no-fund --no-save esbuild@0.25.9 three@0.180.0
  ./node_modules/.bin/esbuild build/entry.js --bundle --format=iife --platform=browser --target=es2020 --minify --outfile=build/avatar-vendor.js
)

mkdir -p "$output_dir"
cp "$work_dir/build/avatar-vendor.js" "$output_dir/avatar-vendor.js"
cp "$work_dir/HeadAudio/dist/headworklet.min.mjs" "$output_dir/headworklet.min.js"
cp "$work_dir/HeadAudio/dist/model-en-mixed.bin" "$output_dir/model-en-mixed.bin"

(
  cd "$output_dir"
  checks=(
    "3aa4136ee2ee06fb922302aa86e51fdac9a463cbdd21868d62919a05dad5dc3a  avatar-vendor.js"
    "37ebeb1d4d7e41fca7d12bb8fb411f7ce6bb21a2589602dec18e0a48b343be55  headworklet.min.js"
    "0358f68989b5861f9b7d18871b010fa6cbf88a53bda4954a954d8c548bbcf251  model-en-mixed.bin"
  )
  printf '%s\n' "${checks[@]}" | sha256sum -c -
)
