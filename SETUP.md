# Medical Scribe Prototype - Phase 1

This is Phase 1 of a local medical scribe prototype that transcribes Hindi, Marathi, and Gujarati speech locally using the `ai4bharat/indic-conformer-600m-multilingual` model.

**Important Data Flow & Privacy Note**:
- **Audio Capture**: The browser captures your microphone audio and forces it to be resampled to exactly 16kHz mono PCM locally inside the browser.
- **Offline Processing**: On the *first run only*, the application requires an internet connection to download the acoustic model (a few hundred MB). Every subsequent run is **fully offline** — no audio or text is ever sent to the cloud.

---

## Step-by-Step Setup Guide

*No prior Python knowledge is required. Just open the `Terminal` application on your Mac and copy-paste the commands exactly as written.*

### 1. Accept the Model License
The transcription model is "gated", meaning you must agree to their terms before downloading.
1. Open this link in your browser: [AI4Bharat Indic Conformer Model](https://huggingface.co/ai4bharat/indic-conformer-600m-multilingual)
2. Create a Hugging Face account (or log in) and click "Agree and access repository" on the page.
3. Go to [Hugging Face Settings > Tokens](https://huggingface.co/settings/tokens) and click **New token** (Read access). Copy this token to your clipboard.

### 2. Prepare the Environment
Open your `Terminal` and copy-paste these commands, pressing Enter after each:

```bash
# Move to the project folder
cd "/Users/abhishekpravinnahire/Desktop/OPD -2"

# Create an isolated Python environment
python3 -m venv venv

# Activate the environment
source venv/bin/activate

# Install the exact required dependencies
pip install -r requirements.txt
```

### 3. Log into Hugging Face
Still in your terminal with the virtual environment activated, run:
```bash
huggingface-cli login
```
When prompted, paste the Access Token you copied earlier. *(Note: When you paste, no characters will show up on the screen for security reasons. Just paste and press Enter).*

---

## How to Run the Application

Any time you want to use the application, open a Terminal and run these two commands:

```bash
cd "/Users/abhishekpravinnahire/Desktop/OPD -2"
source venv/bin/activate
uvicorn main:app --reload
```

> **First Run Notice**: The first time you start the server, it will download the model. You will see a message saying "Downloading transcription model... Please wait as this is a few hundred MB." The terminal might seem paused for a minute or two depending on your internet speed. Subsequent starts will be nearly instant.

Once the terminal says `Application startup complete`, open your web browser and go to:
**http://localhost:8000**

- Select your language.
- Click **Start Recording**.
- Allow microphone permissions if prompted.
- Start speaking! The transcript is safely flushed to disk continuously in the `transcripts/` folder.
