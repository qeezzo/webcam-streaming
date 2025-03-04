const ws = new WebSocket("ws://10.81.90.2:3000");
const pc = new RTCPeerConnection({
    iceServers: [{ urls: "stun:stun.l.google.com:19302" }]
});

let dataChannel;
let videoStream;
let sendFrameStopEvent = true;
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
        videoStream = await navigator.mediaDevices.getUserMedia({ video: { mimeType: "image/jpeg", width, height } });
        videoElement.srcObject = videoStream;
        console.log(`Camera access granted at ${width}x${height}`);

        const track = videoStream.getVideoTracks()[0];
        const capabilities = track.getCapabilities();
        console.log("Camera Capabilities:", capabilities);

        // Create WebRTC Offer
        const offer = await pc.createOffer();
        await pc.setLocalDescription(offer);
        ws.send(JSON.stringify({ sdp: offer }));

    } catch (error) {
        console.error("Error accessing camera:", error);
    }
}

let frameIntervalId;  // Store the interval reference to clear it later
let frameInterval = 1000 / 30; // Frame interval for 30 FPS
let lastFrameTime = 0;  // Last time a frame was sent (in ms)
let frameCount = 0;     // Counter for frames sent in the current second

// Send Frames with Controlled FPS (Handles MJPEG & Raw Automatically)
function startSendingFrames() {
    frameIntervalId = setInterval(() => {
        if (sendFrameStopEvent)
            return;

        if (!videoElement.videoWidth || !videoElement.videoHeight) return;

        // Set canvas size
        canvas.width = videoElement.videoWidth;
        canvas.height = videoElement.videoHeight;

        // Draw the current frame onto the canvas
        ctx.drawImage(videoElement, 0, 0, canvas.width, canvas.height);

        // Send as MJPEG (if available)
        canvas.toBlob((blob) => {
            dataChannel.send(blob);
            // console.log('Frame sent -> ', blob.size);

            // Calculate FPS
            let now = performance.now(); // Get current time in milliseconds
            frameCount++;

            // If more than 1 second has passed, log FPS
            if (now - lastFrameTime >= 1000) {
                let actualFPS = frameCount;
                console.log(`Actual FPS: ${actualFPS}`);
                lastFrameTime = now; // Update last frame time
                frameCount = 0;      // Reset frame count for the next second
            }
        }, "image/jpeg", 0.8);

    }, frameInterval);  // Send a frame every "frameInterval" milliseconds (e.g., 33ms for 30 FPS)
}

// Stop sending frames
function stopSendingFrames() {
    clearInterval(frameIntervalId);
}

// Start streaming when button is clicked
startButton.addEventListener("click", () => {
    sendFrameStopEvent = !sendFrameStopEvent;
    startButton.textContent = sendFrameStopEvent ? "Start Streaming" : "Stop Streaming";
    if (!sendFrameStopEvent) {
        startSendingFrames();  // Begin sending frames when streaming starts
    } else {
        stopSendingFrames();   // Stop sending frames when streaming stops
    }
});

// Trigger a capture with the selected resolution when it changes
resolutionSelect.addEventListener("change", () => {
    startCapture();
})

startCapture();
