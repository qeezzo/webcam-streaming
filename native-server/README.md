# Native WebRTC server

## How to build

1. Build and install gstreamer and libwebsockets using the instruction from the repositories:
gstreamer - https://github.com/GStreamer/gstreamer/blob/main/README.md
libwebsockets - https://github.com/warmcat/libwebsockets/blob/main/READMEs/README.build.md

2. Prepare a build directory using cmake (minimum 3.8):
```shell
mkdir ./build
cd ./build
cmake ../
```

3. Build the project, being inside the ./build directory:
```shell
cmake --build ./
```