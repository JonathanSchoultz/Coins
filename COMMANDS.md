# Coin Reader Useful Commands

Copy/paste these exactly (without shell prompts).

## 1) Service control

```bash
sudo systemctl start coin-reader.service
sudo systemctl stop coin-reader.service
sudo systemctl restart coin-reader.service
sudo systemctl status coin-reader.service
systemctl is-enabled coin-reader.service
```

## 2) Logs (most useful)

```bash
# Last 100 lines
sudo journalctl -u coin-reader.service -n 100 --no-pager

# Follow live logs
sudo journalctl -u coin-reader.service -f

# Only current boot
sudo journalctl -u coin-reader.service -b --no-pager
```

## 3) Print marker lines (for debugging timelines)

```bash
# Print to your terminal
echo "=== TEST START $(date) ==="
printf "audio test: %s\n" "$(date)"

# Print to system journal
logger -t coin-debug "manual test marker: before scan"
sudo journalctl -t coin-debug -n 20 --no-pager
```

## 4) Audio tests

```bash
# Verify command availability
which paplay aplay mpg123

# Known-good system sound
paplay /usr/share/sounds/freedesktop/stereo/complete.oga

# WAV test (if you have one)
aplay ~/Coins/sounds/error.wav

# MP3 test (replace with real file path)
mpg123 -q -o alsa "~/Coins/sounds/your-file.mp3"
```

## 4b) Post-reboot audio sanity check (recommended)

```bash
SINK="alsa_output.usb-C-Media_Electronics_Inc._USB_PnP_Sound_Device-00.analog-stereo-output.2"
ENVV='XDG_RUNTIME_DIR=/run/user/1000 PULSE_SERVER=unix:/run/user/1000/pulse/native'

sudo -u jschoultz env $ENVV pactl set-default-sink "$SINK"
sudo -u jschoultz env $ENVV pactl set-sink-mute "$SINK" 0
sudo -u jschoultz env $ENVV pactl set-sink-volume "$SINK" 100%
sudo -u jschoultz env $ENVV paplay --device "$SINK" /usr/share/sounds/freedesktop/stereo/complete.oga
```

Expected startup log lines from the app:
- `Audio diagnostics: XDG_RUNTIME_DIR=... PULSE_SERVER=...`
- `Audio diagnostics: configured sink is available: ...`

## 5) Check what service is actually running

```bash
sudo systemctl cat coin-reader.service
sudo systemctl show coin-reader.service -p User -p Group -p ExecStart -p WorkingDirectory
```

## 6) Quick deploy/update flow (on Pi)

```bash
cd ~/Coins
git pull
source venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart coin-reader.service
sudo journalctl -u coin-reader.service -n 80 --no-pager
```

## 7) Reset redeemed tags for re-testing

```bash
cd ~/Coins
cp redeemed_tags.yaml redeemed_tags.yaml.bak
: > redeemed_tags.yaml
sudo systemctl restart coin-reader.service
```

## 8) NFC scan test workflow

```bash
sudo journalctl -u coin-reader.service -f
```

Then scan a tag and look for:
- `Tag detected`
- `Processing coin ...`
- `Action ... play_sound`

## 9) Common mistakes to avoid

- Do not paste shell prompts like `(venv) user@host:~$`.
- Do not paste output lines (for example `Mar 15 ...`) back into bash.
- Keep commands one line at a time when troubleshooting.
