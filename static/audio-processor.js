class AudioCaptureProcessor extends AudioWorkletProcessor {
    constructor() {
        super();
        this.chunkSize = 4096;
        this.buffer = new Float32Array(this.chunkSize);
        this.framesInQueue = 0;
    }
    
    process(inputs, outputs, parameters) {
        const input = inputs[0];
        if (input && input.length > 0) {
            const channelData = input[0];
            for (let i = 0; i < channelData.length; i++) {
                this.buffer[this.framesInQueue++] = channelData[i];
                if (this.framesInQueue >= this.chunkSize) {
                    this.port.postMessage(this.buffer.slice(0)); // Send copy to main thread
                    this.framesInQueue = 0;
                }
            }
        }
        return true; // Keep processor alive
    }
}

registerProcessor('audio-capture-processor', AudioCaptureProcessor);
