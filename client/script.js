const ws = new WebSocket("ws://10.81.90.3:3000");
// const ws = new WebSocket("ws://0.0.0.0:3000");
const pc = new RTCPeerConnection({
    // iceServers: [{ urls: "stun:stun.l.google.com:19302" }]
});

let dataChannel;
let videoStream;
const videoElement = document.getElementById("localVideo");
const canvas = document.getElementById("canvas");
const ctx = canvas.getContext("2d");
const startButton = document.getElementById("startButton");
const resolutionSelect = document.getElementById("resolution");

// WebSocket Connection
ws.onopen = () => console.log("WebSocket connected to signaling server");
ws.onmessage = async (message) => {
    const msg = JSON.parse(message.data);
    if (msg.sdp) {
        console.log("Received SDP answer");
        await pc.setRemoteDescription(new RTCSessionDescription(msg.sdp));
    } else if (msg.ice) {
        console.log("Received ICE candidate");
        await pc.addIceCandidate(new RTCIceCandidate(msg.ice));
    }
};

// Handle ICE candidates
pc.onicecandidate = (event) => {
    if (event.candidate) {
        console.log("Sending ICE candidate");
        ws.send(JSON.stringify({ ice: event.candidate }));
    }
};

// WebRTC connection state changes
pc.onconnectionstatechange = () => console.log("Connection state:", pc.connectionState);

// Open DataChannel for MJPEG images
dataChannel = pc.createDataChannel("mjpegStream");
dataChannel.onopen = () => console.log("DataChannel opened");
dataChannel.onerror = (err) => console.error("DataChannel error:", err);
dataChannel.onclose = () => console.log("DataChannel closed");

// Capture Video Stream
async function startCapture() {
    const [width, height] = resolutionSelect.value.split("x").map(Number);
    try {
        videoStream = await navigator.mediaDevices.getUserMedia({
            video: {
                mimeType: "image/jpeg",
                width,
                height
            },
            audio: {
                codec: "opus",
            }
        });
        videoElement.srcObject = videoStream;
        console.log(`Camera access granted at ${width}x${height}`);

        const track = videoStream.getVideoTracks()[0];
        const capabilities = track.getCapabilities();
        console.log("Camera Capabilities:", capabilities);


        // Audio track
        const audioTrack = videoStream.getAudioTracks()[0];
        if (audioTrack) {
            pc.addTrack(audioTrack, videoStream);
            console.log("Audio track added to WebRTC connection.");
        } else {
            console.warn("No audio track found.");
        }

        // Create WebRTC Offer
        const offer = await pc.createOffer();
        await pc.setLocalDescription(offer);
        ws.send(JSON.stringify({ sdp: offer }));

    } catch (error) {
        console.error("Error accessing camera/audio:", error);
    }
}

let sendFrameStopEvent = true; // Flag to control start/stop of streaming
let lastFrameTime = 0;
let frameCount = 0;
const targetFPS = 30;
const frameInterval = 1000 / targetFPS;
console.log(frameInterval);

function sendFrame(timestamp) {
    const elapsedTime = timestamp - lastFrameTime;

    if (elapsedTime >= frameInterval) {
        // Proceed with sending the frame
        canvas.width = videoElement.videoWidth;
        canvas.height = videoElement.videoHeight;
        ctx.drawImage(videoElement, 0, 0, canvas.width, canvas.height);
        canvas.toBlob((blob) => {
            if (dataChannel.readyState !== "open") return;
            dataChannel.send(blob);
        }, "image/jpeg", 0.4);

        // Log FPS for diagnostic purposes
        frameCount++;
        if (elapsedTime >= 1000) {
            console.log(`FPS: ${frameCount}`);
            frameCount = 0;
            lastFrameTime = timestamp;
        }
    }

    // Continue calling the function for the next frame
    requestAnimationFrame(sendFrame);
}

// Start streaming when button is clicked
startButton.addEventListener("click", () => {
    sendFrameStopEvent = !sendFrameStopEvent;  // Toggle the flag
    startButton.textContent = sendFrameStopEvent ? "Start Streaming" : "Stop Streaming";

    if (!sendFrameStopEvent) {
        sendFrame();  // Begin sending frames when streaming starts
    } else {
        cancelAnimationFrame(frameRequestId); // Stop the animation frame when streaming stops
    }
});

// Trigger a capture with the selected resolution when it changes
resolutionSelect.addEventListener("change", () => {
    startCapture();
})

startCapture();
