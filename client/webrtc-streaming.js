const player = document.getElementById("localVideo");
const canvas = document.getElementById("canvas");
const btnLaunch = document.getElementById("startButton");
const ctx = canvas.getContext("2d");

let pc = null;
let ws = null;

let localStream;
let rtcChannel;
let mediaRecorder;
let isStreamActive = false;

let createOffer = async () => {
    pc.onicecandidate = (event) => { 
        if (event.candidate) {
            console.log("ICE candidate: ", event.candidate);
            ws.send(JSON.stringify({ ice: {
                component: event.candidate.component,
                foundation: event.candidate.foundation,
                ip: event.candidate.address,
                port: event.candidate.port,
                priority: event.candidate.priority,
                protocol: event.candidate.protocol,
                type: event.candidate.type,
                relatedAddress: event.candidate.relatedAddress,
                relatedPort: event.candidate.relatedPort,
                sdpMid: event.candidate.sdpMid,
                sdpMLineIndex: event.candidate.sdpMLineIndex,
                tcpType: event.candidate.tcpType
            } }));
        }
    }

    localStream.getTracks().forEach((track) => {
        // if (track.kind === "audio") {
        pc.addTrack(track, localStream);
        //}
    });

    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    ws.send(JSON.stringify(offer));
}

let applyAnswers = async (event) => {
    const handshakeMessage = JSON.parse(event.data);
    if (handshakeMessage.sdp) {
        console.log("Sharing SDP: ", handshakeMessage);
        await pc.setRemoteDescription(new RTCSessionDescription(handshakeMessage));
    } else if (handshakeMessage.ice) {
        console.log("Sharing ICE: ", handshakeMessage.ice);
        await pc.addIceCandidate(new RTCIceCandidate(handshakeMessage.ice));
    }
}

let initiatePeerConnection = () => {
    ws = new WebSocket("ws://192.168.137.243:3000");
    pc = new RTCPeerConnection({ iceServers: [{ urls: "stun:stun.l.google.com:19302" }] });

    ws.onopen = createOffer;
    ws.onmessage = applyAnswers;

    return pc.createDataChannel("stream");
}

let init = async () => {
    localStream = await navigator.mediaDevices.getUserMedia({
        video: {
            mimeType: "image/jpeg",
            width: 800,
            height: 600
        },
        // audio: {
        //     codec: "opus",
        //     sampleRate: 64000,
        //     channelCount: 2,
        //     autoGainControl: false,
        //     echoCancellation: false,
        //     noiseSuppression: false
        // } 
    });
    player.srcObject = localStream;

    rtcChannel = initiatePeerConnection();
    rtcChannel.onopen = () => console.log("DataChannel opened");
    rtcChannel.onerror = (err) => console.error("DataChannel error:", err);
    rtcChannel.onclose = () => console.log("DataChannel closed");
}

let startWebRTCStream = (stream) => {
    mediaRecorder = new MediaRecorder(stream);
    mediaRecorder.ondataavailable = (event) => {
        console.log(event);
    }
    mediaRecorder.start(1000 / 60);
}

let stopWebRTCStream = () => {
    mediaRecorder.stop();
}

// function StartWebRTCStream() {
//     interval = setInterval(() => {
//         canvas.width = 1280;
//         canvas.height = 720;

//         ctx.drawImage(player, 0, 0, canvas.width, canvas.height);
//         canvas.toBlob((blob) => {
//             if (rtcChannel.readyState === "open")
//                 console.log("Blob type: ", blob.type);
//                 rtcChannel.send(blob);
//         }, "image/jpeg", 0.4);
//     }, 1000 / 60);
// }

// function StopWebRTCStream() {
//     clearInterval(interval);
// }

btnLaunch.onclick = function() {
    isStreamActive = !isStreamActive;
    if (isStreamActive) {
        btnLaunch.textContent = "Stop Streaming";
        startWebRTCStream(localStream);
    } else {
        btnLaunch.textContent = "Start Streaming";
        stopWebRTCStream();
    }
}

init();