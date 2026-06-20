let mediaStream = null;
let audioContext = null;
let audioWorkletNode = null;
let websocket = null;
let isRecording = false;

// Audio metrics & queue
let chunksCaptured = 0;
let chunksSent = 0;
let chunkQueue = [];

let originalSoapNote = null;
let consentTimestamp = null;

// Consultation metrics tracking
let consultationClicks = 0;
let stopTimestamp = 0;
let clickCounterActive = false;
let hasUnconfirmedOnLoad = false;


document.addEventListener('click', () => {
    if (clickCounterActive) {
        consultationClicks++;
    }
});

// DOM Elements
const startBtn = document.getElementById('start-btn');
const stopBtn = document.getElementById('stop-btn');
const languageSelect = document.getElementById('language');
const statusIndicator = document.getElementById('status-indicator');
const filePathDisplay = document.getElementById('file-path');
const transcriptBox = document.getElementById('transcript-box');

// SOAP UI Elements
const soapLoading = document.getElementById('soap-loading');
const soapEditor = document.getElementById('soap-editor');
const draftBadge = document.getElementById('draft-badge');

startBtn.addEventListener('click', startRecording);
stopBtn.addEventListener('click', stopRecording);

// Load settings on boot
document.addEventListener('DOMContentLoaded', () => {
    if (window.loadSettings) window.loadSettings();
    initializeRecoveryAndAutosave();
    const visitDateInput = document.getElementById('visit_date');
    if (visitDateInput && !visitDateInput.value) {
        visitDateInput.valueAsDate = new Date();
    }
});

window.toggleRecordingBtn = async function() {
    const consent = document.getElementById('consent-checkbox').checked;
    
    if (consent) {
        // Request microphone permission immediately
        try {
            statusIndicator.textContent = "Requesting mic...";
            // If we don't already have a stream, request it
            if (!mediaStream) {
                mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
            }
            statusIndicator.textContent = "Mic ready";
            startBtn.disabled = false;
        } catch (err) {
            console.error('Error accessing microphone:', err);
            let errorMsg = 'Could not access microphone.';
            
            if (err.name === 'NotAllowedError' || err.name === 'SecurityError') {
                errorMsg = 'Microphone permission denied. Please allow microphone access in your browser settings (specifically for this URL) to use the scribe.';
            } else if (err.name === 'NotFoundError') {
                errorMsg = 'No microphone detected. Please plug in a microphone and try again.';
            } else {
                errorMsg = `Microphone error: ${err.message || err.name}. Please ensure permissions are granted.`;
            }
            
            statusIndicator.textContent = "Mic Blocked";
            startBtn.disabled = true;
            document.getElementById('consent-checkbox').checked = false; // Uncheck it since they can't proceed
            alert(errorMsg);
        }
    } else {
        startBtn.disabled = true;
        // Optionally release the mic if they uncheck consent? We'll leave it for now to avoid re-prompting.
    }
};

async function startRecording() {
    try {
        // If for some reason we lost the stream, re-request it
        if (!mediaStream) {
            mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
        }
        
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws/audio`;
        websocket = new WebSocket(wsUrl);
        
        websocket.onopen = () => {
            isRecording = true;
            consentTimestamp = new Date().toISOString(); // lock in the consent time
            statusIndicator.textContent = "Recording...";
            statusIndicator.classList.add('recording');
            startBtn.disabled = true;
            stopBtn.disabled = false;
            languageSelect.disabled = true;
            document.getElementById('consent-checkbox').disabled = true;
            
            transcriptBox.innerHTML = '';
            
            // Hide previous SOAP note
            soapEditor.style.display = 'none';
            soapLoading.style.display = 'none';
            draftBadge.style.display = 'none';
            
            websocket.send(languageSelect.value);
            setupAudioProcessing();
        };

        websocket.onmessage = (event) => {
            const data = JSON.parse(event.data);
            if (data.type === 'info') {
                filePathDisplay.textContent = data.message;
            } else if (data.type === 'transcript') {
                const textNode = document.createTextNode(data.text + " ");
                transcriptBox.appendChild(textNode);
                transcriptBox.scrollTop = transcriptBox.scrollHeight;
            }
        };

        websocket.onclose = () => {
            if (isRecording) {
                stopRecording();
            }
        };

        websocket.onerror = (error) => {
            console.error("WebSocket Error:", error);
            if (isRecording) {
                stopRecording();
            }
        };

    } catch (err) {
        console.error('Error accessing microphone:', err);
        let errorMsg = 'Could not access microphone.';
        
        if (err.name === 'NotAllowedError' || err.name === 'SecurityError') {
            errorMsg = 'Microphone permission denied. Please allow microphone access in your browser settings to use the scribe.';
        } else if (err.name === 'NotFoundError') {
            errorMsg = 'No microphone detected. Please plug in a microphone and try again.';
        } else {
            errorMsg = `Microphone error: ${err.message || err.name}. Please ensure permissions are granted.`;
        }
        
        statusIndicator.textContent = "Mic Error";
        alert(errorMsg);
    }
}

async function setupAudioProcessing() {
    audioContext = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
    
    try {
        await audioContext.audioWorklet.addModule('/static/audio-processor.js');
    } catch (e) {
        console.error("Failed to load audio worklet:", e);
        return;
    }
    
    const source = audioContext.createMediaStreamSource(mediaStream);
    audioWorkletNode = new AudioWorkletNode(audioContext, 'audio-capture-processor');
    
    chunksCaptured = 0;
    chunksSent = 0;
    chunkQueue = [];
    
    audioWorkletNode.port.onmessage = (e) => {
        if (!isRecording) return;
        
        chunksCaptured++;
        
        const floatData = e.data;
        const int16Buffer = new Int16Array(floatData.length);
        for (let i = 0; i < floatData.length; i++) {
            let s = Math.max(-1, Math.min(1, floatData[i]));
            int16Buffer[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
        }
        
        // Queue if websocket isn't ready
        if (websocket && websocket.readyState === WebSocket.OPEN) {
            // Drain queue first
            while (chunkQueue.length > 0) {
                websocket.send(chunkQueue.shift());
                chunksSent++;
            }
            // Send current
            websocket.send(int16Buffer.buffer);
            chunksSent++;
        } else {
            chunkQueue.push(int16Buffer.buffer);
        }
    };
    
    source.connect(audioWorkletNode);
    audioWorkletNode.connect(audioContext.destination);
}

async function stopRecording() {
    isRecording = false;
    statusIndicator.textContent = "Ready";
    statusIndicator.classList.remove('recording');
    startBtn.disabled = false;
    stopBtn.disabled = true;
    languageSelect.disabled = false;
    
    if (audioWorkletNode) {
        audioWorkletNode.disconnect();
        audioWorkletNode = null;
    }
    
    console.log(`[FRONTEND] Audio chunks captured: ${chunksCaptured}, sent: ${chunksSent}, left in queue: ${chunkQueue.length}`);
    
    if (audioContext) {
        audioContext.close();
        audioContext = null;
    }
    
    if (mediaStream) {
        mediaStream.getTracks().forEach(track => track.stop());
        mediaStream = null;
    }
    
    if (websocket) {
        if (websocket.readyState === WebSocket.OPEN) {
            websocket.close();
        }
        websocket = null;
    }

    // Phase 2: Call Gemini for SOAP generation
    const fullTranscript = transcriptBox.textContent.trim();
    console.log(`[FRONTEND] STOP pressed, transcript length = ${fullTranscript.length}`);
    
    // Start consultation metrics tracking
    stopTimestamp = Date.now();
    consultationClicks = 0;
    clickCounterActive = true;
    const metricsEl = document.getElementById('consultation-metrics');
    if (metricsEl) {
        metricsEl.style.display = 'none'; // hide until print-ready
    }
    
    generateSoapNote(fullTranscript);
}

// --- Schedule H / H1 Drug Checker ---
const SCHEDULE_H_INGREDIENTS = [
    // Antibiotics
    'amoxicillin', 'clavulanate', 'ciprofloxacin', 'azithromycin', 'ofloxacin', 'cefixime', 
    'ceftriaxone', 'cefuroxime', 'doxycycline', 'levofloxacin', 'metronidazole', 'nitrofurantoin', 
    'clarithromycin', 'sulfamethoxazole', 'erythromycin', 'piperacillin', 'tazobactam',
    // Benzodiazepines & Sedatives
    'alprazolam', 'clonazepam', 'diazepam', 'lorazepam', 'zolpidem', 'midazolam', 
    'chlordiazepoxide', 'nitrazepam', 'phenobarbital', 'etizolam',
    // Gabapentinoids, Narcotics & Restricted
    'gabapentin', 'pregabalin', 'tramadol', 'ketamine', 'codeine'
];

window.isScheduleH = function(brand, generic) {
    const brandLower = brand ? brand.toLowerCase() : '';
    const genericLower = generic ? generic.toLowerCase() : '';
    return SCHEDULE_H_INGREDIENTS.some(ing => 
        brandLower.includes(ing) || genericLower.includes(ing)
    );
};

function preserveDosageUnit(dosageVal, topMatch) {
    if (!dosageVal) return "";
    let valStr = String(dosageVal).trim();
    if (valStr.toLowerCase() === "not specified" || valStr.toLowerCase() === "none identified" || valStr === "") {
        return valStr;
    }
    const isPureNumber = /^\d+(\.\d+)?$/.test(valStr);
    if (isPureNumber && topMatch && topMatch.strength) {
        const unitMatch = topMatch.strength.match(/(mg|ml|mcg|g|iu|%)/i);
        if (unitMatch) {
            valStr = valStr + unitMatch[0].toLowerCase();
        } else {
            valStr = valStr + "mg";
        }
    }
    return valStr;
}

window.useSuggestedName = function(index, name) {
    const nameInput = document.getElementById(`med-name-${index}`);
    if (nameInput) {
        nameInput.value = name;
        // Log correction from spoken name to suggested name
        if (originalSoapNote && originalSoapNote.medications && originalSoapNote.medications[index]) {
            const spokenName = originalSoapNote.medications[index].name || "";
            logCorrection(spokenName, name, `med_name_${index}`);
        }
    }
};

function decodeJSONString(str) {
    return str
        .replace(/\\n/g, '\n')
        .replace(/\\t/g, '\t')
        .replace(/\\"/g, '"')
        .replace(/\\\\/g, '\\');
}

function extractField(jsonStr, fieldName) {
    const closedRegex = new RegExp(`"${fieldName}"\\s*:\\s*"((?:[^"\\\\]|\\\\.)*)"`);
    const closedMatch = jsonStr.match(closedRegex);
    if (closedMatch) {
        return decodeJSONString(closedMatch[1]);
    }
    
    const openRegex = new RegExp(`"${fieldName}"\\s*:\\s*"((?:[^"\\\\]|\\\\.)*)$`);
    const openMatch = jsonStr.match(openRegex);
    if (openMatch) {
        return decodeJSONString(openMatch[1]);
    }
    
    return null;
}

function updateProgressiveFields(jsonStr) {
    const fields = ['chief_complaint', 'history', 'clinical_findings', 'diagnosis', 'advice', 'unstructured_notes'];
    fields.forEach(id => {
        const val = extractField(jsonStr, id);
        const el = document.getElementById(id);
        if (el && val !== null) {
            el.value = val;
            el.dataset.original = val;
        }
    });
}

function handleFinalSoapNote(rawData) {
    const data = rawData.soap_note || rawData.soap || rawData;
    originalSoapNote = JSON.parse(JSON.stringify(data)); // Deep copy for comparison
    
    document.getElementById('chief_complaint').value = data.chief_complaint || '';
    document.getElementById('chief_complaint').dataset.original = data.chief_complaint || '';
    
    document.getElementById('history').value = data.history || '';
    document.getElementById('history').dataset.original = data.history || '';
    
    document.getElementById('clinical_findings').value = data.clinical_findings || '';
    document.getElementById('clinical_findings').dataset.original = data.clinical_findings || '';
    
    document.getElementById('diagnosis').value = data.diagnosis || '';
    document.getElementById('diagnosis').dataset.original = data.diagnosis || '';
    
    document.getElementById('advice').value = data.advice || '';
    document.getElementById('advice').dataset.original = data.advice || '';
    
    document.getElementById('unstructured_notes').value = data.unstructured_notes || '';
    document.getElementById('unstructured_notes').dataset.original = data.unstructured_notes || '';
    
    // Setup blur listeners for logging text fields
    ['chief_complaint', 'history', 'clinical_findings', 'diagnosis', 'advice', 'unstructured_notes'].forEach(id => {
        const el = document.getElementById(id);
        if (el) {
            el.onblur = function() {
                if (this.value !== this.dataset.original) {
                    logCorrection(this.dataset.original, this.value, id);
                    this.dataset.original = this.value; // update so we don't spam logs
                }
            };
        }
    });
    
    // Populate medications
    const medList = document.getElementById('medications-list');
    medList.innerHTML = '';
    
    hasUnconfirmedOnLoad = false;
    
    if (data.medications && Array.isArray(data.medications)) {
        data.medications.forEach((med, index) => {
            const medDiv = document.createElement('div');
            medDiv.id = `med-card-${index}`;
            
            // Engine fallback handling
            let topMatch = null;
            let alternatives = [];
            let isNoMatch = false;
            
            if (med.matches && med.matches.length > 0) {
                topMatch = med.matches[0];
                alternatives = med.matches.slice(1);
                if (topMatch.score === 0 || topMatch.score < 60 || med.is_unverified) {
                    isNoMatch = true;
                }
            } else {
                isNoMatch = true;
            }
            
            const brandDisplay = topMatch && !isNoMatch ? topMatch.brand : "";
            let genericDisplay = topMatch && topMatch.generic && !isNoMatch ? ` — ${topMatch.generic}` : "";
            if (topMatch && topMatch.therapeutic_class && topMatch.therapeutic_class !== 'Unknown' && !isNoMatch) {
                genericDisplay += ` — ${topMatch.therapeutic_class}`;
            }
            
            // Schedule H warning badge in editor
            const isSchH = topMatch && !isNoMatch && window.isScheduleH(topMatch.brand, topMatch.generic);
            const schHBadge = isSchH ? '<span class="sch-h-badge">⚠️ Schedule H/H1</span>' : '';
            
            // Parse fields
            const getVal = (field) => {
                if (!field) return '';
                let val = field;
                if (typeof field === 'object' && field !== null) {
                    val = field.value;
                }
                if (val === null || val === undefined) return '';
                const valStr = String(val).trim();
                if (valStr.toLowerCase() === 'null' || valStr.toLowerCase() === 'none' || valStr.toLowerCase() === 'undefined') {
                    return '';
                }
                return valStr;
            };
            const getExplicit = (field) => field && typeof field === 'object' ? field.explicitly_stated : true;
            
            const dosageVal = getVal(med.dosage);
            const dosageExplicit = getExplicit(med.dosage);
            const dosageGuessed = !dosageExplicit && dosageVal !== "Not specified" && dosageVal !== "None identified" && dosageVal !== "";
            
            const freqVal = getVal(med.frequency);
            const freqExplicit = getExplicit(med.frequency);
            const freqGuessed = !freqExplicit && freqVal !== "Not specified" && freqVal !== "None identified" && freqVal !== "";
            
            const routeVal = getVal(med.route);
            const routeExplicit = getExplicit(med.route);
            const routeGuessed = !routeExplicit && routeVal !== "Not specified" && routeVal !== "None identified" && routeVal !== "";
            
            const durationVal = getVal(med.duration);
            const durationExplicit = getExplicit(med.duration);
            const durationGuessed = !durationExplicit && durationVal !== "Not specified" && durationVal !== "None identified" && durationVal !== "";
            
            // NEW SAFETY AUTO-CONFIRM LOGIC
            const canAutoConfirm = 
                topMatch && 
                !isNoMatch && 
                !med.is_unverified &&
                dosageVal && dosageVal.trim() !== "" && dosageVal !== "Not specified" && dosageVal !== "None identified" && dosageExplicit && !dosageGuessed &&
                freqVal && freqVal.trim() !== "" && freqVal !== "Not specified" && freqVal !== "None identified" && freqExplicit && !freqGuessed &&
                routeVal && routeVal.trim() !== "" && routeVal !== "Not specified" && routeVal !== "None identified" && routeExplicit && !routeGuessed &&
                durationVal && durationVal.trim() !== "" && durationVal !== "Not specified" && durationVal !== "None identified" && durationExplicit && !durationGuessed;
            
            let initialShowConfirm = 'flex';
            let initialShowConfirmedOverlay = 'none';
            let cardClass = 'med-confirmation-card';
            let headerClass = topMatch && topMatch.confidence === 'high' ? 'confidence-high' : 'confidence-uncertain';
            
            if (canAutoConfirm) {
                cardClass += ' confirmed-state';
                headerClass = 'med-header confidence-high';
                initialShowConfirm = 'none';
                initialShowConfirmedOverlay = 'flex';
            } else {
                hasUnconfirmedOnLoad = true;
            }
            
            medDiv.className = cardClass;
            
            let headerHTML = "";
            if (isNoMatch) {
                const suggestions = (med.matches || []).filter(m => m.brand && m.brand !== "No reliable match — enter manually" && m.score >= 80);
                
                let suggestionsHTML = "";
                if (suggestions.length > 0) {
                    suggestionsHTML = `
                        <span class="suggestions-container" id="med-suggestions-${index}" style="font-size: 0.85rem; color: #6c757d; margin-left: 12px;">
                            Did you mean: ${suggestions.map(alt => `
                                <button type="button" class="suggestion-btn" style="padding: 2px 6px; font-size: 0.8rem; text-decoration: underline; color: #007bff; border: none; background: none; cursor: pointer;" onclick="useSuggestedName(${index}, '${alt.brand.replace(/'/g, "\\'")}')">${alt.brand}</button>
                            `).join(', ')}?
                        </span>
                    `;
                }
                
                headerHTML = `
                    <div class="med-header confidence-uncertain" id="med-header-${index}" style="display: flex; align-items: center; flex-wrap: wrap; gap: 8px;">
                        <input type="text" class="med-name-input" id="med-name-${index}" value="${med.name || ''}" placeholder="Medication Name" style="font-weight: bold; font-size: 1rem; padding: 4px 8px; border: 1px solid #ced4da; border-radius: 4px; width: 250px;" ${canAutoConfirm ? 'disabled' : ''}>
                        <span class="unverified-badge" style="background-color: #fff3cd; color: #856404; border: 1px solid #ffeeba; padding: 3px 8px; border-radius: 12px; font-size: 0.8rem; font-weight: 500; display: inline-flex; align-items: center; gap: 4px;">⚠️ Not in database — verify</span>
                        ${suggestionsHTML}
                    </div>
                `;
            } else {
                headerHTML = `
                    <div class="med-header ${headerClass}" id="med-header-${index}">
                        <strong>${brandDisplay}</strong><span class="generic-text">${genericDisplay}</span>
                        ${schHBadge}
                    </div>
                `;
            }
            
            const renderInput = (id, isGuessed, val, placeholder) => {
                const disabledAttr = canAutoConfirm ? 'disabled' : '';
                if (isGuessed) {
                    return `
                        <div class="input-wrapper">
                            <input type="text" id="${id}" class="ai-guessed" value="${val}" placeholder="${placeholder}" oninput="clearAIGuess(this)" ${disabledAttr}>
                            <button class="accept-btn" onclick="clearAIGuessById('${id}')" title="Accept AI Suggestion" style="${canAutoConfirm ? 'display: none;' : ''}">✓ Accept</button>
                        </div>
                    `;
                } else {
                    return `<input type="text" id="${id}" value="${val}" placeholder="${placeholder}" ${disabledAttr}>`;
                }
            };
            
            medDiv.innerHTML = `
                ${headerHTML}
                
                <div class="med-inputs" id="med-inputs-${index}">
                    ${renderInput(`med-dosage-${index}`, dosageGuessed, dosageVal, 'Dosage (e.g. 500mg)')}
                    ${renderInput(`med-freq-${index}`, freqGuessed, freqVal, 'Freq (e.g. BD)')}
                    ${renderInput(`med-route-${index}`, routeGuessed, routeVal, 'Route (e.g. Oral)')}
                    ${renderInput(`med-duration-${index}`, durationGuessed, durationVal, 'Duration (e.g. 5 days)')}
                </div>
                
                <div class="med-actions" id="med-actions-${index}" style="display: ${initialShowConfirm}">
                    <button class="btn primary btn-sm" onclick="confirmMedication(${index})">Confirm</button>
                    <button class="btn danger btn-sm" onclick="rejectMedication(${index})">Reject</button>
                </div>
                
                <div class="med-reject-panel" id="med-reject-${index}" style="display: none">
                    <p class="reject-title">Select Alternative or Enter Manually:</p>
                    <div class="alt-options">
                        ${alternatives.map((alt, i) => `
                            <label>
                                <input type="radio" name="alt-group-${index}" value="alt-${i}">
                                ${alt.brand} <span class="alt-generic">— ${alt.generic}</span>
                            </label>
                        `).join('')}
                        <label>
                            <input type="radio" name="alt-group-${index}" value="manual" ${isNoMatch ? 'checked' : ''}>
                            Manual Entry: <input type="text" id="med-manual-${index}" placeholder="Type drug name" value="${med.name || ''}" ${isNoMatch ? '' : `onclick="document.querySelector('input[name=\\'alt-group-${index}\\'][value=\\'manual\\']').checked = true;"`}>
                        </label>
                    </div>
                    <button class="btn primary btn-sm" onclick="updateRejectedMedication(${index})">Update Match</button>
                </div>
                
                <div class="med-confirmed-overlay" id="med-confirmed-${index}" style="display: ${initialShowConfirmedOverlay};">
                    <span class="icon">✅</span> Confirmed
                </div>
            `;
            
            medList.appendChild(medDiv);
            window[`medData_${index}`] = { alternatives, topMatch };
        });
    }
    
    if (medList.children.length === 0) {
        medList.innerHTML = '<p style="color: #6c757d; font-size: 0.9rem; margin: 0;">None identified</p>';
    }
    
    checkPrintStatus();
}

async function generateSoapNote(transcript) {
    console.log("[FRONTEND] generateSoapNote called");
    soapLoading.style.display = 'flex';
    soapEditor.style.display = 'none';
    draftBadge.style.display = 'none';

    // Clear UI inputs first
    document.getElementById('chief_complaint').value = '';
    document.getElementById('history').value = '';
    document.getElementById('clinical_findings').value = '';
    document.getElementById('diagnosis').value = '';
    document.getElementById('advice').value = '';
    document.getElementById('unstructured_notes').value = '';
    document.getElementById('medications-list').innerHTML = '';

    try {
        console.log("[FRONTEND] Sending fetch request to /api/generate_note_stream");
        const response = await fetch('/api/generate_note_stream', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ transcript: transcript })
        });
        
        console.log(`[FRONTEND] Received response with status: ${response.status}`);
        
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        
        soapLoading.style.display = 'none';
        soapEditor.style.display = 'block';
        draftBadge.style.display = 'inline-block';
        
        const reader = response.body.getReader();
        const decoder = new TextDecoder('utf-8');
        let buffer = '';
        let fullScribeText = '';
        
        while (true) {
            const { value, done } = await reader.read();
            if (done) break;
            
            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop(); // keep the last partial line in buffer
            
            for (const line of lines) {
                if (!line.trim()) continue;
                try {
                    const parsed = JSON.parse(line);
                    if (parsed.type === 'chunk') {
                        fullScribeText += parsed.text;
                        updateProgressiveFields(fullScribeText);
                    } else if (parsed.type === 'final') {
                        handleFinalSoapNote(parsed.data);
                    }
                } catch (e) {
                    console.error("Error parsing stream line:", e, line);
                }
            }
        }
    } catch (err) {
        console.error("Error generating SOAP note:", err);
        document.getElementById('chief_complaint').value = "AI summary unavailable — please fill manually.";
        document.getElementById('history').value = "AI summary unavailable — please fill manually.";
        document.getElementById('clinical_findings').value = "AI summary unavailable — please fill manually.";
        document.getElementById('diagnosis').value = "None identified";
        document.getElementById('advice').value = "AI summary unavailable — please fill manually.";
        document.getElementById('medications-list').innerHTML = '<p style="color: #6c757d; font-size: 0.9rem; margin: 0;">None identified</p>';
        checkPrintStatus();
    } finally {
        soapLoading.style.display = 'none';
        soapEditor.style.display = 'block';
        draftBadge.style.display = 'inline-block';
    }
}

// Global functions for inline HTML event handlers
window.confirmMedication = function(index) {
    let valid = true;
    let hasUnverifiedGuesses = false;
    
    // We need to query the actual inputs, not just rely on getElementById since they might be wrapped
    const dosageInput = document.getElementById(`med-dosage-${index}`);
    const freqInput = document.getElementById(`med-freq-${index}`);
    const routeInput = document.getElementById(`med-route-${index}`);
    const durationInput = document.getElementById(`med-duration-${index}`);
    const nameInput = document.getElementById(`med-name-${index}`);
    
    if (nameInput) {
        const cleanedName = nameInput.value.trim().toLowerCase();
        if (!nameInput.value.trim() || cleanedName === "null" || cleanedName === "undefined" || cleanedName === "none") {
            nameInput.classList.add('error-border');
            valid = false;
        } else {
            nameInput.classList.remove('error-border');
        }
    }
    
    [dosageInput, freqInput, routeInput, durationInput].forEach(el => {
        const cleanedVal = el.value.trim().toLowerCase();
        if (!el.value.trim() || el.value.trim() === "Not specified" || cleanedVal === "null" || cleanedVal === "undefined" || cleanedVal === "none") {
            el.classList.add('error-border');
            valid = false;
        } else {
            el.classList.remove('error-border');
        }
        
        if (el.classList.contains('ai-guessed')) {
            hasUnverifiedGuesses = true;
        }
    });
    
    if (hasUnverifiedGuesses) {
        alert("Please verify the highlighted AI-suggested values by clicking 'Accept' or editing them before confirming.");
        return;
    }
    
    if (!valid) return;
    
    // Logging logic: Compare inputs with original AI values if available
    if (originalSoapNote && originalSoapNote.medications && originalSoapNote.medications[index]) {
        const origMed = originalSoapNote.medications[index];
        const origDosage = origMed.dosage?.value || "";
        const origFreq = origMed.frequency?.value || "";
        const origRoute = origMed.route?.value || "";
        const origDuration = origMed.duration?.value || "";
        
        if (nameInput && nameInput.value.trim() !== (origMed.name || "")) {
            logCorrection(origMed.name || "", nameInput.value.trim(), `med_name_${index}`);
        }
        if (dosageInput.value !== origDosage) logCorrection(origDosage, dosageInput.value, `med_dosage_${index}`);
        if (freqInput.value !== origFreq) logCorrection(origFreq, freqInput.value, `med_freq_${index}`);
        if (routeInput.value !== origRoute) logCorrection(origRoute, routeInput.value, `med_route_${index}`);
        if (durationInput.value !== origDuration) logCorrection(origDuration, durationInput.value, `med_duration_${index}`);
    }
    
    // Lock it
    [dosageInput, freqInput, routeInput, durationInput].forEach(el => el.disabled = true);
    if (nameInput) {
        nameInput.disabled = true;
        nameInput.style.backgroundColor = 'transparent';
        nameInput.style.border = 'none';
        nameInput.style.color = '#155724';
        
        const suggestionsEl = document.getElementById(`med-suggestions-${index}`);
        if (suggestionsEl) {
            suggestionsEl.style.display = 'none';
        }
    }
    
    document.getElementById(`med-actions-${index}`).style.display = 'none';
    document.getElementById(`med-confirmed-${index}`).style.display = 'flex';
    document.getElementById(`med-card-${index}`).classList.add('confirmed-state');
    document.getElementById(`med-header-${index}`).className = 'med-header confidence-high';
    
    checkPrintStatus();
};

window.rejectMedication = function(index) {
    document.getElementById(`med-actions-${index}`).style.display = 'none';
    document.getElementById(`med-header-${index}`).style.display = 'none';
    document.getElementById(`med-reject-${index}`).style.display = 'block';
};

window.updateRejectedMedication = function(index) {
    const dosageInput = document.getElementById(`med-dosage-${index}`);
    const freqInput = document.getElementById(`med-freq-${index}`);
    const routeInput = document.getElementById(`med-route-${index}`);
    const durationInput = document.getElementById(`med-duration-${index}`);
    
    let valid = true;
    let hasUnverifiedGuesses = false;
    
    [dosageInput, freqInput, routeInput, durationInput].forEach(el => {
        if (!el.value.trim() || el.value.trim() === "Not specified") {
            el.classList.add('error-border');
            valid = false;
        } else {
            el.classList.remove('error-border');
        }
        
        if (el.classList.contains('ai-guessed')) {
            hasUnverifiedGuesses = true;
        }
    });
    
    if (hasUnverifiedGuesses) {
        alert("Please verify the highlighted AI-suggested values by clicking 'Accept' or editing them before confirming.");
        return;
    }
    
    const selected = document.querySelector(`input[name="alt-group-${index}"]:checked`);
    if (!selected) {
        alert("Please select an alternative or enter manually.");
        return;
    }
    
    let newBrand = "";
    let newGeneric = "";
    let isManualUnverified = false;
    
    if (selected.value === "manual") {
        const manualInput = document.getElementById(`med-manual-${index}`);
        if (!manualInput.value.trim()) {
            manualInput.classList.add('error-border');
            valid = false;
        } else {
            manualInput.classList.remove('error-border');
            newBrand = manualInput.value.trim();
            isManualUnverified = true;
        }
    } else {
        const altIdx = parseInt(selected.value.split('-')[1]);
        const alt = window[`medData_${index}`].alternatives[altIdx];
        newBrand = alt.brand;
        newGeneric = alt.generic ? ` — ${alt.generic}` : "";
        if (alt.therapeutic_class && alt.therapeutic_class !== 'Unknown') {
            newGeneric += ` — ${alt.therapeutic_class}`;
        } else if (alt.therapeutic_class === 'Unknown') {
            console.log(`[THERAPEUTIC CLASS] Generic: ${alt.generic} is Unknown`);
        }
    }
    
    if (!valid) return;
    
    // Logging logic
    if (originalSoapNote && originalSoapNote.medications && originalSoapNote.medications[index]) {
        const origMed = originalSoapNote.medications[index];
        logCorrection(origMed.name, newBrand, `med_name_${index}`);
        
        const origDosage = origMed.dosage?.value || "";
        const origFreq = origMed.frequency?.value || "";
        const origRoute = origMed.route?.value || "";
        const origDuration = origMed.duration?.value || "";
        
        if (dosageInput.value !== origDosage) logCorrection(origDosage, dosageInput.value, `med_dosage_${index}`);
        if (freqInput.value !== origFreq) logCorrection(origFreq, freqInput.value, `med_freq_${index}`);
        if (routeInput.value !== origRoute) logCorrection(origRoute, routeInput.value, `med_route_${index}`);
        if (durationInput.value !== origDuration) logCorrection(origDuration, durationInput.value, `med_duration_${index}`);
    }
    
    // Update header
    const header = document.getElementById(`med-header-${index}`);
    if (isManualUnverified) {
        header.innerHTML = `<strong>${newBrand}</strong> <span class="unverified-text" style="color: #dc3545; font-size: 0.85rem; font-weight: normal; margin-left: 8px;">(Not database-verified)</span>`;
    } else {
        header.innerHTML = `<strong>${newBrand}</strong><span class="generic-text">${newGeneric}</span>`;
    }
    header.className = 'med-header confidence-high';
    header.style.display = 'block';
    
    // Lock it
    [dosageInput, freqInput, routeInput, durationInput].forEach(el => el.disabled = true);
    document.getElementById(`med-reject-${index}`).style.display = 'none';
    document.getElementById(`med-confirmed-${index}`).style.display = 'flex';
    document.getElementById(`med-card-${index}`).classList.add('confirmed-state');
    
    checkPrintStatus();
};

window.checkPrintStatus = function() {
    const medCards = document.querySelectorAll('.med-confirmation-card');
    let allConfirmed = true;
    medCards.forEach(card => {
        if (!card.classList.contains('confirmed-state')) {
            allConfirmed = false;
        }
    });
    
    const printBtn = document.getElementById('print-btn');
    const helperText = document.getElementById('print-helper-text');
    if (allConfirmed) {
        printBtn.disabled = false;
        helperText.style.display = 'none';
        if (hasUnconfirmedOnLoad) {
            printBtn.textContent = "Print / Save PDF";
        } else {
            printBtn.textContent = "Confirm All & Print";
        }
        
        // Trigger metrics display if active
        if (clickCounterActive) {
            const duration = ((Date.now() - stopTimestamp) / 1000).toFixed(1);
            clickCounterActive = false; // stop tracking
            const metricsEl = document.getElementById('consultation-metrics');
            const timeToRxVal = document.getElementById('time-to-rx-val');
            const clicksVal = document.getElementById('clicks-val');
            if (metricsEl && timeToRxVal && clicksVal) {
                timeToRxVal.textContent = `${duration}s`;
                clicksVal.textContent = consultationClicks;
                metricsEl.style.display = 'block';
            }
        }
    } else {
        printBtn.disabled = true;
        helperText.style.display = 'block';
        printBtn.textContent = "Print / Save PDF";
    }
};

window.printPrescription = function() {
    let patientNameInput = document.getElementById('patient_name');
    let patientAgeInput = document.getElementById('patient_age');
    if (patientNameInput && !patientNameInput.value.trim()) {
        patientNameInput.value = "Walk-in Patient";
    }
    if (patientAgeInput && !patientAgeInput.value.trim()) {
        patientAgeInput.value = "Not specified";
    }
    const patientName = patientNameInput ? patientNameInput.value.trim() : "Walk-in Patient";
    const patientAge = patientAgeInput ? patientAgeInput.value.trim() : "Not specified";

    // Populate Patient Info
    document.getElementById('print_patient_name').textContent = patientName;
    document.getElementById('print_patient_age').textContent = patientAge;
    
    const dateVal = document.getElementById('visit_date').value;
    document.getElementById('print_date').textContent = dateVal ? new Date(dateVal).toLocaleDateString() : new Date().toLocaleDateString();

    // Populate Clinical Info
    document.getElementById('print_cc').textContent = document.getElementById('chief_complaint').value || 'None';
    document.getElementById('print_findings').textContent = document.getElementById('clinical_findings').value || 'None';
    document.getElementById('print_diagnosis').textContent = document.getElementById('diagnosis').value || 'None identified';
    document.getElementById('print_advice').textContent = document.getElementById('advice').value || 'None';

    const unNotes = document.getElementById('unstructured_notes').value || '';
    document.getElementById('print_unstructured_notes').textContent = unNotes || 'None';
    document.getElementById('print_unstructured_notes_container').style.display = unNotes ? 'block' : 'none';

    // Populate Medications and check for Schedule H/H1
    const medCards = document.querySelectorAll('.med-confirmation-card.confirmed-state');
    const tbody = document.getElementById('print_meds_body');
    tbody.innerHTML = '';
    
    let hasScheduleH = false;
    const medsArray = [];
    
    if (medCards.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;">No medications prescribed</td></tr>';
    } else {
        medCards.forEach((card) => {
            // Find inputs within this card
            const inputs = card.querySelectorAll('.med-inputs input');
            const dosage = inputs[0] ? inputs[0].value : '';
            const freq = inputs[1] ? inputs[1].value : '';
            const route = inputs[2] ? inputs[2].value : '';
            const duration = inputs[3] ? inputs[3].value : '';
            
            // Reconstruct the full drug name from the header
            const nameInput = card.querySelector('.med-name-input');
            let brandStr = "";
            let genericStr = "";
            if (nameInput) {
                brandStr = nameInput.value.trim();
            } else {
                const brandEl = card.querySelector('.med-header strong');
                brandStr = brandEl ? brandEl.textContent.trim() : '';
                const genericEl = card.querySelector('.med-header .generic-text');
                genericStr = genericEl ? genericEl.textContent.trim() : '';
            }
            
            const headerStr = genericStr ? `${brandStr}${genericStr}` : brandStr;
            
            // Check Schedule H/H1 warning
            if (window.isScheduleH(brandStr, genericStr)) {
                hasScheduleH = true;
            }
            
            medsArray.push({ name: headerStr, dosage, frequency: freq, route, duration });
            
            const displayVal = (val) => {
                if (!val) return '—';
                const cleaned = String(val).trim();
                if (cleaned === "" || cleaned.toLowerCase() === "null" || cleaned.toLowerCase() === "undefined" || cleaned.toLowerCase() === "none") {
                    return '—';
                }
                return cleaned;
            };

            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td><strong>${headerStr}</strong></td>
                <td>${displayVal(dosage)}</td>
                <td>${displayVal(freq)}</td>
                <td>${displayVal(route)}</td>
                <td>${displayVal(duration)}</td>
            `;
            tbody.appendChild(tr);
        });
    }

    // Toggle Schedule H warning container
    const warningBox = document.getElementById('print_warning_box');
    if (warningBox) {
        warningBox.style.display = hasScheduleH ? 'block' : 'none';
    }

    const sessionData = {
        patient_info: {
            name: patientName,
            age: patientAge,
            date: dateVal || new Date().toISOString()
        },
        soap_note: {
            chief_complaint: document.getElementById('chief_complaint').value,
            history: document.getElementById('history').value,
            clinical_findings: document.getElementById('clinical_findings').value,
            diagnosis: document.getElementById('diagnosis').value,
            advice: document.getElementById('advice').value,
            unstructured_notes: document.getElementById('unstructured_notes').value
        },
        medications: medsArray,
        timestamp: new Date().toISOString(),
        consent_timestamp: consentTimestamp || new Date().toISOString()
    };

    // Trigger Print Dialog only after saving session
    fetch('/api/save_session', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(sessionData)
    }).then(() => {
        localStorage.removeItem('activeOPDSession'); // Clear saved session
        window.print();
    }).catch(err => {
        console.error("Failed to save session:", err);
        window.print(); // print anyway
    });
};

window.clearAIGuess = function(el) {
    if (el && el.classList.contains('ai-guessed')) {
        el.classList.remove('ai-guessed');
        // Hide the accept button if it exists
        if (el.nextElementSibling && el.nextElementSibling.classList.contains('accept-btn')) {
            el.nextElementSibling.style.display = 'none';
        }
    }
};

window.clearAIGuessById = function(id) {
    const el = document.getElementById(id);
    if (el) {
        window.clearAIGuess(el);
    }
};

// Logging System
function logCorrection(original, corrected, field) {
    if (original === corrected) return;
    const payload = {
        original_value: original,
        corrected_value: corrected,
        field_type: field,
        timestamp: new Date().toISOString()
    };
    fetch('/api/log_correction', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    }).catch(err => console.error("Log failed:", err));
}

// Settings System
window.openSettings = function() {
    document.getElementById('settings-modal').style.display = 'flex';
};
window.closeSettings = function() {
    document.getElementById('settings-modal').style.display = 'none';
};
window.saveSettings = function() {
    const profile = {
        name: document.getElementById('setting_doc_name').value || 'Dr. Example Scribe',
        qual: document.getElementById('setting_doc_qual').value || 'MBBS, MD (General Medicine)',
        reg: document.getElementById('setting_doc_reg').value || 'Reg No: 123456',
        council: document.getElementById('setting_doc_council').value || 'Maharashtra Medical Council',
        clinic_name: document.getElementById('setting_clinic_name').value || 'Clinic Address Line 1',
        clinic_address: document.getElementById('setting_clinic_address').value || 'City, State, 123456',
        clinic_phone: document.getElementById('setting_clinic_phone').value || 'Ph: +91 9876543210'
    };
    localStorage.setItem('doctorProfile', JSON.stringify(profile));
    loadSettings();
    closeSettings();
};
window.loadSettings = function() {
    const profileStr = localStorage.getItem('doctorProfile');
    if (profileStr) {
        const profile = JSON.parse(profileStr);
        document.getElementById('setting_doc_name').value = profile.name;
        document.getElementById('setting_doc_qual').value = profile.qual;
        document.getElementById('setting_doc_reg').value = profile.reg;
        document.getElementById('setting_doc_council').value = profile.council || '';
        document.getElementById('setting_clinic_name').value = profile.clinic_name;
        document.getElementById('setting_clinic_address').value = profile.clinic_address;
        document.getElementById('setting_clinic_phone').value = profile.clinic_phone;
        
        // Apply to print view
        document.getElementById('print_doctor_name').textContent = profile.name;
        document.getElementById('print_doctor_qual').textContent = profile.qual;
        document.getElementById('print_doctor_reg').textContent = profile.reg;
        document.getElementById('print_doctor_council').textContent = profile.council || '';
        document.getElementById('print_clinic_name').textContent = profile.clinic_name;
        document.getElementById('print_clinic_address').textContent = profile.clinic_address;
        document.getElementById('print_clinic_phone').textContent = profile.clinic_phone;
    }
};

// Metrics System
window.openMetrics = function() {
    document.getElementById('metrics-modal').style.display = 'flex';
    document.getElementById('metrics-loading').style.display = 'block';
    document.getElementById('metrics-content').style.display = 'none';
    
    fetch('/api/metrics')
        .then(res => res.json())
        .then(data => {
            document.getElementById('metric-consults').textContent = data.total_consults;
            document.getElementById('metric-edits').textContent = data.average_edits_per_prescription;
            document.getElementById('metric-field').textContent = data.most_corrected_field;
            document.getElementById('metrics-loading').style.display = 'none';
            document.getElementById('metrics-content').style.display = 'block';
        })
        .catch(err => {
            document.getElementById('metrics-loading').textContent = "Failed to load metrics.";
            console.error(err);
        });
};
window.closeMetrics = function() {
    document.getElementById('metrics-modal').style.display = 'none';
};

// --- QW-2: Auto-Save & Crash Recovery Logic ---

window.saveActiveSession = function() {
    const transcriptBox = document.getElementById('transcript-box');
    if (!transcriptBox) return;
    
    // Ignore initial placeholder
    let transcript = transcriptBox.textContent.trim();
    if (transcriptBox.querySelector('.placeholder')) {
        transcript = '';
    }
    
    const soapEditor = document.getElementById('soap-editor');
    const soapVisible = soapEditor && soapEditor.style.display === 'block';
    
    const sessionState = {
        patient_name: document.getElementById('patient_name')?.value || '',
        patient_age: document.getElementById('patient_age')?.value || '',
        visit_date: document.getElementById('visit_date')?.value || '',
        transcript: transcript,
        consentTimestamp: consentTimestamp,
        consentChecked: document.getElementById('consent-checkbox')?.checked || false,
        originalSoapNote: originalSoapNote,
        soapVisible: soapVisible
    };
    
    if (soapVisible) {
        sessionState.soap_note = {
            chief_complaint: document.getElementById('chief_complaint')?.value || '',
            history: document.getElementById('history')?.value || '',
            clinical_findings: document.getElementById('clinical_findings')?.value || '',
            diagnosis: document.getElementById('diagnosis')?.value || '',
            advice: document.getElementById('advice')?.value || '',
            unstructured_notes: document.getElementById('unstructured_notes')?.value || ''
        };
        
        // Save medications card states
        const medCards = document.querySelectorAll('.med-confirmation-card');
        const meds = [];
        medCards.forEach((card, idx) => {
            const dosageInput = document.getElementById(`med-dosage-${idx}`);
            const freqInput = document.getElementById(`med-freq-${idx}`);
            const routeInput = document.getElementById(`med-route-${idx}`);
            const durationInput = document.getElementById(`med-duration-${idx}`);
            const isConfirmed = card.classList.contains('confirmed-state');
            const nameInput = document.getElementById(`med-name-${idx}`);
            
            let medName = "";
            let isUnverified = false;
            
            if (nameInput) {
                medName = nameInput.value.trim();
                isUnverified = true;
            } else {
                const strongEl = card.querySelector('.med-header strong');
                medName = strongEl ? strongEl.innerText.trim() : "";
            }
            
            const isRejected = document.getElementById(`med-reject-${idx}`)?.style.display === 'block';
            
            meds.push({
                name: medName,
                dosage: dosageInput ? dosageInput.value : '',
                frequency: freqInput ? freqInput.value : '',
                route: routeInput ? routeInput.value : '',
                duration: durationInput ? durationInput.value : '',
                isConfirmed: isConfirmed,
                isRejected: isRejected,
                is_unverified: isUnverified
            });
        });
        sessionState.medications = meds;
    }
    
    localStorage.setItem('activeOPDSession', JSON.stringify(sessionState));
};

window.restoreSession = function(session) {
    if (session.patient_name) document.getElementById('patient_name').value = session.patient_name;
    if (session.patient_age) document.getElementById('patient_age').value = session.patient_age;
    if (session.visit_date) document.getElementById('visit_date').value = session.visit_date;
    
    const transcriptBox = document.getElementById('transcript-box');
    if (session.transcript && transcriptBox) {
        transcriptBox.innerHTML = '';
        const textNode = document.createTextNode(session.transcript);
        transcriptBox.appendChild(textNode);
    }
    
    if (session.consentChecked) {
        const consentCheckbox = document.getElementById('consent-checkbox');
        if (consentCheckbox) {
            consentCheckbox.checked = true;
            toggleRecordingBtn();
        }
    }
    
    consentTimestamp = session.consentTimestamp;
    originalSoapNote = session.originalSoapNote;
    
    const soapEditor = document.getElementById('soap-editor');
    const draftBadge = document.getElementById('draft-badge');
    
    if (session.soapVisible && session.soap_note && soapEditor) {
        soapEditor.style.display = 'block';
        if (draftBadge) draftBadge.style.display = 'inline-block';
        
        document.getElementById('chief_complaint').value = session.soap_note.chief_complaint || '';
        document.getElementById('chief_complaint').dataset.original = session.soap_note.chief_complaint || '';
        
        document.getElementById('history').value = session.soap_note.history || '';
        document.getElementById('history').dataset.original = session.soap_note.history || '';
        
        document.getElementById('clinical_findings').value = session.soap_note.clinical_findings || '';
        document.getElementById('clinical_findings').dataset.original = session.soap_note.clinical_findings || '';
        
        document.getElementById('diagnosis').value = session.soap_note.diagnosis || '';
        document.getElementById('diagnosis').dataset.original = session.soap_note.diagnosis || '';
        
        document.getElementById('advice').value = session.soap_note.advice || '';
        document.getElementById('advice').dataset.original = session.soap_note.advice || '';
        
        document.getElementById('unstructured_notes').value = session.soap_note.unstructured_notes || '';
        document.getElementById('unstructured_notes').dataset.original = session.soap_note.unstructured_notes || '';
        
        // Setup blur listeners for logging text fields
        ['chief_complaint', 'history', 'clinical_findings', 'diagnosis', 'advice', 'unstructured_notes'].forEach(id => {
            const el = document.getElementById(id);
            if (el) {
                el.onblur = function() {
                    if (this.value !== this.dataset.original) {
                        logCorrection(this.dataset.original, this.value, id);
                        this.dataset.original = this.value;
                    }
                };
            }
        });
        
        // Re-populate medications list if saved
        if (session.medications && session.medications.length > 0) {
            const medList = document.getElementById('medications-list');
            if (medList) {
                medList.innerHTML = '';
                hasUnconfirmedOnLoad = false;
                
                const cleanRestoredVal = (val) => {
                    if (val === null || val === undefined) return '';
                    const valStr = String(val).trim();
                    if (valStr.toLowerCase() === 'null' || valStr.toLowerCase() === 'undefined' || valStr.toLowerCase() === 'none') {
                        return '';
                    }
                    return valStr;
                };

                session.medications.forEach((med, index) => {
                    if (!med.isConfirmed) {
                        hasUnconfirmedOnLoad = true;
                    }
                    const medDiv = document.createElement('div');
                    medDiv.className = `med-confirmation-card ${med.isConfirmed ? 'confirmed-state' : ''}`;
                    medDiv.id = `med-card-${index}`;
                    
                    const confidenceClass = med.isConfirmed ? 'confidence-high' : 'confidence-uncertain';
                    const initialShowReject = med.isRejected ? 'block' : 'none';
                    const initialShowConfirm = (med.isConfirmed || med.isRejected) ? 'none' : 'flex';
                    const initialShowConfirmedOverlay = med.isConfirmed ? 'flex' : 'none';
                    
                    const isSchH = window.isScheduleH(med.name, '');
                    const schHBadge = isSchH ? '<span class="sch-h-badge">⚠️ Schedule H/H1</span>' : '';
                    
                    let headerHTML = "";
                    if (med.is_unverified) {
                        let suggestionsHTML = "";
                        if (!med.isConfirmed && originalSoapNote && originalSoapNote.medications && originalSoapNote.medications[index]) {
                            const origMed = originalSoapNote.medications[index];
                            const suggestions = (origMed.matches || []).filter(m => m.brand && m.brand !== "No reliable match — enter manually" && m.score >= 80);
                            if (suggestions.length > 0) {
                                suggestionsHTML = `
                                    <span class="suggestions-container" id="med-suggestions-${index}" style="font-size: 0.85rem; color: #6c757d; margin-left: 12px;">
                                        Did you mean: ${suggestions.map(alt => `
                                            <button type="button" class="suggestion-btn" style="padding: 2px 6px; font-size: 0.8rem; text-decoration: underline; color: #007bff; border: none; background: none; cursor: pointer;" onclick="useSuggestedName(${index}, '${alt.brand.replace(/'/g, "\\'")}')">${alt.brand}</button>
                                        `).join(', ')}?
                                    </span>
                                `;
                            }
                        }
                        
                        headerHTML = `
                            <div class="med-header confidence-uncertain" id="med-header-${index}" style="display: flex; align-items: center; flex-wrap: wrap; gap: 8px;">
                                <input type="text" class="med-name-input" id="med-name-${index}" value="${med.name || ''}" ${med.isConfirmed ? 'disabled style="font-weight: bold; font-size: 1rem; padding: 4px 8px; border: none; background-color: transparent; color: #155724; width: 250px;"' : 'style="font-weight: bold; font-size: 1rem; padding: 4px 8px; border: 1px solid #ced4da; border-radius: 4px; width: 250px;"'}>
                                <span class="unverified-badge" style="background-color: #fff3cd; color: #856404; border: 1px solid #ffeeba; padding: 3px 8px; border-radius: 12px; font-size: 0.8rem; font-weight: 500; display: inline-flex; align-items: center; gap: 4px;">⚠️ Not in database — verify</span>
                                ${suggestionsHTML}
                            </div>
                        `;
                    } else {
                        headerHTML = `
                            <div class="med-header ${confidenceClass}" id="med-header-${index}">
                                <strong>${med.name}</strong>
                                ${schHBadge}
                            </div>
                        `;
                    }

                    medDiv.innerHTML = `
                        ${headerHTML}
                        
                        <div class="med-inputs" id="med-inputs-${index}">
                            <input type="text" id="med-dosage-${index}" value="${cleanRestoredVal(med.dosage)}" placeholder="Dosage (e.g. 500mg)" ${med.isConfirmed ? 'disabled' : ''}>
                            <input type="text" id="med-freq-${index}" value="${cleanRestoredVal(med.frequency)}" placeholder="Freq (e.g. BD)" ${med.isConfirmed ? 'disabled' : ''}>
                            <input type="text" id="med-route-${index}" value="${cleanRestoredVal(med.route)}" placeholder="Route (e.g. Oral)" ${med.isConfirmed ? 'disabled' : ''}>
                            <input type="text" id="med-duration-${index}" value="${cleanRestoredVal(med.duration)}" placeholder="Duration (e.g. 5 days)" ${med.isConfirmed ? 'disabled' : ''}>
                        </div>
                        
                        <div class="med-actions" id="med-actions-${index}" style="display: ${initialShowConfirm}">
                            <button class="btn primary btn-sm" onclick="confirmMedication(${index})">Confirm</button>
                            <button class="btn danger btn-sm" onclick="rejectMedication(${index})">Reject</button>
                        </div>
                        
                        <div class="med-reject-panel" id="med-reject-${index}" style="display: ${initialShowReject}">
                            <p class="reject-title">Select Alternative or Enter Manually:</p>
                            <div class="alt-options">
                                <label>
                                    <input type="radio" name="alt-group-${index}" value="manual" checked>
                                    Manual Entry: <input type="text" id="med-manual-${index}" placeholder="Type drug name">
                                </label>
                            </div>
                            <button class="btn primary btn-sm" onclick="updateRejectedMedication(${index})">Update Match</button>
                        </div>
                        
                        <div class="med-confirmed-overlay" id="med-confirmed-${index}" style="display: ${initialShowConfirmedOverlay};">
                            <span class="icon">✅</span> Confirmed
                        </div>
                    `;
                    
                    medList.appendChild(medDiv);
                });
                checkPrintStatus();
            }
        }
    }
};

window.initializeRecoveryAndAutosave = function() {
    // Check for recovered session
    const savedSession = localStorage.getItem('activeOPDSession');
    if (savedSession) {
        try {
            const session = JSON.parse(savedSession);
            if (session.transcript || session.patient_name || (session.soap_note && session.soapVisible)) {
                // Show a recovery banner at the top of the left panel (below the header)
                const leftPanel = document.querySelector('.left-panel');
                if (leftPanel) {
                    const header = leftPanel.querySelector('header');
                    const banner = document.createElement('div');
                    banner.id = 'recovery-banner';
                    banner.className = 'recovery-banner';
                    banner.innerHTML = `
                        <div class="recovery-text">
                            ⚠️ <strong>Unsaved Session Found:</strong> We detected an unfinished consultation for patient "${session.patient_name || 'Unknown'}".
                        </div>
                        <div class="recovery-buttons">
                            <button class="btn btn-sm primary" id="restore-session-btn">Restore</button>
                            <button class="btn btn-sm outline" id="discard-session-btn">Discard</button>
                        </div>
                    `;
                    leftPanel.insertBefore(banner, header ? header.nextSibling : leftPanel.firstChild);
                    
                    document.getElementById('restore-session-btn').onclick = () => {
                        window.restoreSession(session);
                        banner.remove();
                    };
                    
                    document.getElementById('discard-session-btn').onclick = () => {
                        localStorage.removeItem('activeOPDSession');
                        banner.remove();
                    };
                }
            }
        } catch (e) {
            console.error("Failed to parse saved session", e);
        }
    }

    // Set up auto-save interval every 10 seconds
    setInterval(() => {
        window.saveActiveSession();
    }, 10000);

    // Save on beforeunload
    window.addEventListener('beforeunload', () => {
        window.saveActiveSession();
    });
};

