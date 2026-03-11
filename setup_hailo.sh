#!/bin/bash
# setup_hailo.sh
# Sets up the Hailo AI HAT+ and Ollama on a fresh Raspberry Pi 5.
# Run once as your normal user (not root). Will ask for sudo when needed.
#
# What this does:
#   1. Enables the Hailo PCIe overlay in /boot/firmware/config.txt
#   2. Installs HailoRT driver + Python bindings
#   3. Installs Ollama (local LLM runner)
#   4. Pulls the phi3:mini model (good speed/quality balance on Pi 5 + Hailo)
#   5. Enables Ollama as a systemd service so it starts on boot
#
# After running this script, reboot once, then start the quest log:
#   python3 server.py

set -e  # exit on any error

OLLAMA_MODEL="phi3:mini"   # change to "gemma3:1b" or "tinyllama" if preferred

echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║   QUESTLOG — Hailo AI HAT+ Setup                ║"
echo "╚══════════════════════════════════════════════════╝"
echo ""

# ── 1. System update ──────────────────────────────────────────────────────────
echo "▶ Updating system packages..."
sudo apt-get update -qq
sudo apt-get upgrade -y -qq

# ── 2. Enable Hailo PCIe overlay ─────────────────────────────────────────────
echo "▶ Enabling Hailo PCIe overlay..."

CONFIG=/boot/firmware/config.txt

if grep -q "dtoverlay=hailo-module" "$CONFIG" 2>/dev/null; then
    echo "  Hailo overlay already present in $CONFIG — skipping."
else
    echo "" | sudo tee -a "$CONFIG" > /dev/null
    echo "# Hailo AI HAT+" | sudo tee -a "$CONFIG" > /dev/null
    echo "dtoverlay=hailo-module" | sudo tee -a "$CONFIG" > /dev/null
    echo "  Added dtoverlay=hailo-module to $CONFIG"
fi

# Enable PCIe Gen 3 for better throughput (optional but recommended)
if ! grep -q "dtparam=pciex1_gen=3" "$CONFIG" 2>/dev/null; then
    echo "dtparam=pciex1_gen=3" | sudo tee -a "$CONFIG" > /dev/null
    echo "  Enabled PCIe Gen 3"
fi

# ── 3. Install HailoRT ────────────────────────────────────────────────────────
echo "▶ Installing HailoRT..."

# Add Hailo apt repo if not already present
if [ ! -f /etc/apt/sources.list.d/hailo.list ]; then
    curl -sSL https://hailo.ai/repo/hailo-repo.gpg | sudo tee /usr/share/keyrings/hailo-keyring.gpg > /dev/null
    echo "deb [signed-by=/usr/share/keyrings/hailo-keyring.gpg] https://hailo.ai/repo/apt stable main" \
        | sudo tee /etc/apt/sources.list.d/hailo.list > /dev/null
    sudo apt-get update -qq
fi

sudo apt-get install -y -qq hailo-all

echo "  HailoRT installed."

# ── 4. Install Ollama ─────────────────────────────────────────────────────────
echo "▶ Installing Ollama..."

if command -v ollama &> /dev/null; then
    echo "  Ollama already installed — skipping."
else
    curl -fsSL https://ollama.com/install.sh | sh
    echo "  Ollama installed."
fi

# ── 5. Enable Ollama service ──────────────────────────────────────────────────
echo "▶ Enabling Ollama service..."
sudo systemctl enable ollama
sudo systemctl start ollama

# Wait for Ollama to be ready
echo "  Waiting for Ollama to start..."
for i in $(seq 1 15); do
    if curl -sf http://localhost:11434/api/tags > /dev/null 2>&1; then
        echo "  Ollama is running."
        break
    fi
    sleep 2
done

# ── 6. Pull the model ─────────────────────────────────────────────────────────
echo "▶ Pulling model: $OLLAMA_MODEL"
echo "  (This may take a few minutes on first run...)"
ollama pull "$OLLAMA_MODEL"
echo "  Model ready."

# ── 7. Quick test ─────────────────────────────────────────────────────────────
echo ""
echo "▶ Running quick inference test..."
TEST_RESPONSE=$(ollama run "$OLLAMA_MODEL" "Reply with exactly: OK" 2>/dev/null || echo "FAILED")
if echo "$TEST_RESPONSE" | grep -qi "ok"; then
    echo "  ✓ Model responded correctly."
else
    echo "  ⚠ Model test gave unexpected response: $TEST_RESPONSE"
    echo "    This may be fine — try running the server anyway."
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║   Setup complete!                               ║"
echo "╠══════════════════════════════════════════════════╣"
echo "║                                                 ║"
echo "║   REBOOT YOUR PI NOW:                           ║"
echo "║     sudo reboot                                 ║"
echo "║                                                 ║"
echo "║   Then start the quest log server:              ║"
echo "║     cd questlog && python3 server.py            ║"
echo "║                                                 ║"
echo "║   Verify the HAT is detected after reboot:      ║"
echo "║     hailortcli fw-control identify              ║"
echo "║                                                 ║"
echo "╚══════════════════════════════════════════════════╝"
echo ""
