#!/usr/bin/env python3
"""
tty_test.py — low-level tester for the Pi <-> Arduino USB serial link.

This bypasses the C++ robot binary entirely: it opens the Arduino's serial
port directly (the same /dev/ttyACM0 that NavigationCoordinator uses) and
lets you push the one-character motor commands the firmware understands:

    U = accelerate (forward)   D = decelerate (reverse)
    L = rotate left            R = rotate right
    S = stop (both treads to neutral)

Use it to confirm the port works, the cable is data-capable, the Arduino is
flashed with robot.ino, and the Sabertooth reacts — before bringing up the
full server.  Pure standard library, no pyserial needed.

USAGE
    ./scripts/tty_test.py --list                 # show candidate serial ports
    ./scripts/tty_test.py                         # auto-pick a port, interactive
    ./scripts/tty_test.py --port /dev/ttyACM0     # pick a port explicitly
    ./scripts/tty_test.py --baud 115200 --interactive
    ./scripts/tty_test.py --preflight             # interactive Sabertooth dry-fire check (do this first!)
    ./scripts/tty_test.py --demo                  # ramp both treads 25/50/75/100% fwd, then rev, then stop
    ./scripts/tty_test.py --demo --hold 4         #   ... holding each speed 4 s
    ./scripts/tty_test.py --send UUUS             # fire a fixed byte string, exit
    ./scripts/tty_test.py --no-reset-wait         # skip the 2 s post-open pause

INTERACTIVE KEYS
    arrow keys / W A S D ... U D L R   (W/Up=forward, S/Down=reverse, A/D rotate)
    space ........................... STOP
    enter ........................... STOP (alias)
    ? ............................... print this help again
    q / Esc ......................... quit (sends STOP first)

SAFETY: put the treads up on blocks the first time.  Opening the port resets
the Arduino, which re-attaches the servo outputs at neutral; expect a brief
twitch.  Each U/D/L/R nudges a tread by ~3/255, so tap several times.
"""

import argparse
import glob
import os
import select
import sys
import termios
import time
import tty

# Bytes the robot.ino firmware acts on.  Anything else is ignored by the board.
CMD = {"U": b"U", "D": b"D", "L": b"L", "R": b"R", "S": b"S"}
NAME = {"U": "FORWARD", "D": "REVERSE", "L": "ROTATE-LEFT", "R": "ROTATE-RIGHT", "S": "STOP"}

# Mirror of robot.ino's motor state so the script can report "where the treads
# should be" — the firmware has no absolute-level command, only ±STEP nudges.
NEUTRAL = 90          # Servo.write(90) ≈ 1500 us = stop
STEP = 3              # each U/D/L/R changes a channel by this much
_level = {"L": NEUTRAL, "R": NEUTRAL}    # left / right channel, 0..180


def _pulse_us(level):
    """Servo angle -> approx R/C pulse width the Sabertooth sees."""
    return round(1000 + level * (1000.0 / 180.0))


def _apply(key):
    """Update the modeled motor levels exactly as robot.ino would."""
    L, R = _level["L"], _level["R"]
    if key == "U":      L -= STEP; R -= STEP        # both toward forward
    elif key == "D":    L += STEP; R += STEP        # both toward reverse
    elif key == "L":    L += STEP; R -= STEP        # pivot left
    elif key == "R":    L -= STEP; R += STEP        # pivot right
    elif key == "S":    L = R = NEUTRAL
    _level["L"] = max(0, min(180, L))
    _level["R"] = max(0, min(180, R))


def _level_str():
    L, R = _level["L"], _level["R"]
    def desc(v):
        if v == NEUTRAL: return "stop"
        return f"{'fwd' if v < NEUTRAL else 'rev'} {abs(v - NEUTRAL) * 100 // NEUTRAL}%"
    return (f"L={L:>3}({_pulse_us(L)}us,{desc(L)})  R={R:>3}({_pulse_us(R)}us,{desc(R)})")

BAUD_CONST = {
    9600: termios.B9600, 19200: termios.B19200, 38400: termios.B38400,
    57600: termios.B57600, 115200: termios.B115200, 230400: termios.B230400,
}


def candidate_ports():
    ports = sorted(glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*"))
    # by-id symlinks are stable across reboots; show them too if present.
    ports += sorted(glob.glob("/dev/serial/by-id/*"))
    seen, out = set(), []
    for p in ports:
        real = os.path.realpath(p)
        if real in seen:
            continue
        seen.add(real)
        out.append(p)
    return out


def list_ports():
    ports = candidate_ports()
    if not ports:
        print("No /dev/ttyACM* or /dev/ttyUSB* devices found.")
        print("  - Is the Arduino plugged into the Pi with a DATA (not charge-only) cable?")
        print("  - `dmesg | tail` right after plugging it in should show 'cdc_acm' / 'ttyACM0'.")
        return
    print("Candidate serial ports:")
    for p in ports:
        try:
            st = os.stat(p)
            who = "writable" if os.access(p, os.W_OK) else "NOT writable (need 'dialout' group?)"
            extra = f"  realpath={os.path.realpath(p)}" if p.startswith("/dev/serial") else ""
            print(f"  {p}  (mode {oct(st.st_mode & 0o777)}, {who}){extra}")
        except OSError as e:
            print(f"  {p}  (stat failed: {e})")
    print("\nArduino Unos/Leonardos show up as /dev/ttyACM*; CH340/CP210x clones as /dev/ttyUSB*.")


def open_port(path, baud, reset_wait):
    if baud not in BAUD_CONST:
        sys.exit(f"Unsupported baud {baud}; pick one of {sorted(BAUD_CONST)}")
    try:
        fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    except OSError as e:
        sys.exit(f"Cannot open {path}: {e}\n"
                 f"  - `./scripts/tty_test.py --list` to see what's available\n"
                 f"  - Permission denied? `sudo usermod -aG dialout $USER` then log out/in\n"
                 f"  - Busy? something else holds the port (robot wifi-server / ble_server.py); "
                 f"`lsof {path}`")

    # Mirror NavigationCoordinator::Start(): 8N1, raw, no flow control.
    attrs = termios.tcgetattr(fd)
    iflag, oflag, cflag, lflag, ispeed, ospeed, cc = attrs
    iflag &= ~(termios.IXON | termios.IXOFF | termios.IXANY | termios.IGNBRK |
               termios.BRKINT | termios.PARMRK | termios.ISTRIP | termios.INLCR |
               termios.IGNCR | termios.ICRNL)
    oflag &= ~termios.OPOST
    cflag &= ~(termios.CSIZE | termios.PARENB | termios.CSTOPB | termios.CRTSCTS)
    cflag |= termios.CS8 | termios.CLOCAL | termios.CREAD
    lflag &= ~(termios.ECHO | termios.ECHONL | termios.ICANON | termios.ISIG | termios.IEXTEN)
    cc[termios.VMIN] = 0
    cc[termios.VTIME] = 0
    b = BAUD_CONST[baud]
    termios.tcsetattr(fd, termios.TCSANOW, [iflag, oflag, cflag, lflag, b, b, cc])

    if reset_wait:
        print(f"Opened {path} @ {baud} 8N1. Waiting 2 s for the Arduino bootloader to hand off...")
        time.sleep(2.0)
    termios.tcflush(fd, termios.TCIOFLUSH)
    return fd


def send(fd, key, quiet=False):
    """key is one of U/D/L/R/S; write the single command byte."""
    os.write(fd, CMD[key])
    _apply(key)
    if not quiet:
        print(f"  TX  '{key}'  -> {NAME[key]:<12}  {_level_str()}")


def send_n(fd, key, n, gap=0.04):
    """Send the same command n times (gap seconds apart) and report once."""
    for _ in range(n):
        os.write(fd, CMD[key])
        _apply(key)
        time.sleep(gap)
    print(f"  TX  '{key}' x{n:<3}-> {NAME[key]:<12}  {_level_str()}")


def drain_rx(fd, label="  RX  "):
    """Print anything the Arduino sent back (robot.ino is silent, but a wrong
    sketch / bootloader chatter would show up here)."""
    try:
        data = os.read(fd, 256)
    except (BlockingIOError, OSError):
        return
    if data:
        printable = data.decode("latin-1").replace("\r", "\\r").replace("\n", "\\n")
        print(f"{label}{data!r}  ({printable})")


def run_send_string(fd, s):
    for ch in s.upper():
        if ch in CMD:
            send(fd, ch)
            time.sleep(0.15)
            drain_rx(fd)
        elif ch in (" ", ","):
            time.sleep(0.3)
        else:
            print(f"  (skip {ch!r}: not a U/D/L/R/S command)")
    # leave the robot stopped
    send(fd, "S")


def hold_signal(fd, key, n, gap=0.04, hold_secs=0.0, label=None):
    """Send 'key' n times to reach a level, then sit there for hold_secs by
    letting the Arduino keep emitting the last servo position (it does this
    on its own — no further bytes needed).  Useful for probing D10/D11 with
    a multimeter, or letting the Sabertooth autocal sample an endpoint."""
    if label:
        print(f"  {label}")
    for _ in range(n):
        os.write(fd, CMD[key])
        _apply(key)
        time.sleep(gap)
    print(f"  TX  '{key}' x{n:<3}-> {NAME[key]:<12}  {_level_str()}")
    if hold_secs > 0:
        # Just sleep — the Arduino's Servo lib keeps generating the pulse
        # at 50 Hz forever, so the Sabertooth keeps seeing this level.
        end = time.time() + hold_secs
        while time.time() < end:
            time.sleep(0.1)
            drain_rx(fd)


def _prompt(msg):
    """Wait for the user to press Enter; returns the line they typed."""
    try:
        return input(f"\n  >>> {msg}\n      [press Enter to continue, or type something and Enter] ")
    except (EOFError, KeyboardInterrupt):
        print()
        raise SystemExit("Preflight aborted.")


def run_preflight(fd):
    """Interactive Sabertooth + Arduino preflight.  The user keeps motors
    mechanically safe (disconnected from the Sabertooth M1A/M1B/M2A/M2B
    screw terminals, OR treads up on blocks) and powers the Sabertooth on
    AFTER the Arduino is already idling.  The script then walks through a
    graduated test, prompting the user at each step to confirm what the
    LEDs / motors do.  Catches the common DIP-switch and wiring mistakes
    before any real driving."""
    border = "=" * 70
    print("\n" + border)
    print(" SABERTOOTH PREFLIGHT  —  dry-fire check before driving")
    print(border)
    print("""
 What this does:
   1. Confirms the Pi <-> Arduino serial link is healthy.
   2. Walks you through powering the Sabertooth in the right order so its
      auto-calibration (DIP6 = ON / Standard R/C) samples your Arduino's
      idle pulse as 'center'.
   3. Has you confirm the Sabertooth's status LEDs at each step.  Those
      two LEDs tell you what mode it thinks it's in and whether it's
      seeing a valid R/C signal.
   4. Drives a slow walk through the throttle range so you can see the
      Sabertooth react WITHOUT the motors moving meaningfully (treads up
      on blocks, OR motor leads unscrewed from the Sabertooth output
      terminals).

 LED quick reference (Sabertooth 2x12 v1.0):
   STATUS 1 (green)  solid       = R/C signal good, motors armed
                     blinking    = no/invalid R/C signal (Standard R/C only:
                                   this is the failsafe — motors disabled)
   STATUS 2 (yellow/red) off     = nothing wrong
                     solid/blink = battery too low, too high, over-temp,
                                   or (in Lithium mode) detecting cell count
   Both LEDs blinking at boot    = auto-calibration in progress (Standard R/C)

 SAFETY:
   *** For this preflight, EITHER unscrew the motor leads from the
       Sabertooth's M1A/M1B/M2A/M2B output terminals, OR keep the
       treads off the ground on blocks. ***
""")
    _prompt("Ready?  Confirm: motors safe (disconnected or on blocks), "
            "Sabertooth power switch OFF, Arduino plugged into the Pi.")

    # ---- step 1: serial link health ----
    print("\n[1/6] Pi -> Arduino serial link")
    # Send an 'S' just to make sure the write succeeds; the port has already
    # been opened by main(), so any failure here is a kernel/permission issue.
    try:
        send(fd, "S")
    except OSError as e:
        print(f"  !!! write to serial port failed: {e}")
        print(f"  This is a Pi-side problem, not Sabertooth.  Aborting preflight.")
        return
    print("  OK — wrote 'S' (neutral) to the Arduino without errors.")
    print("  The Arduino is now holding both channels at 1500 us (servo write(90)).")

    _prompt("Step 2: Sabertooth still OFF.  Look at the Sabertooth — "
            "both status LEDs should be DARK.  Confirm.")

    # ---- step 2: power on Sabertooth ----
    print("\n[2/6] Sabertooth power-on  (autocal will sample center)")
    print("  Because the Arduino is already idling at 1500 us, the Sabertooth's")
    print("  first pulse reading == your Arduino's center.  That becomes its")
    print("  saved center for this and future sessions.")
    _prompt("Turn ON the Sabertooth's main power switch NOW.  Wait ~2 seconds.")

    print("""
  Expected LEDs at this point (Standard R/C, DIP6 = ON):
    STATUS 1 (green)  : should go SOLID within ~1-2 s.
                        Solid = 'I see a valid R/C signal on S1 + S2'.
    STATUS 2 (yellow) : SHORT blink burst at boot is normal — it's auto-
                        detecting LiPo cell count (DIP3 = ON).  Should
                        then go OFF / nearly off.

  If STATUS 1 stays BLINKING:
    - No R/C signal reaching it.  Check D10 -> S1, D11 -> S2, and the
      ground from Arduino GND -> Sabertooth 0V (the pin next to S1/S2).
    - OR you set DIP1/DIP2 wrong and it's in Analog/Serial mode instead
      of R/C.  Re-check: DIP1 = OFF, DIP2 = ON.

  If STATUS 2 is solid or blinks repeatedly:
    - Pack voltage is out of range (6-24 V for 2x12 v1.0).  Check pack.
    - In Lithium mode (DIP3 = ON), a wildly off-range pack will refuse to
      arm.  Verify your LiPo voltage with a meter; it must be > 3.0 V/cell
      *and* below the controller's input ceiling.
""")
    ans = _prompt("Describe STATUS 1 and STATUS 2 (e.g. '1 solid, 2 off' / "
                  "'1 blinking, 2 off' / '1 solid, 2 blinking 3x repeating').")
    print(f"  noted: {ans!r}")
    if "blink" in ans.lower() and "1" in ans.lower():
        print("  !!! STATUS 1 blinking = the Sabertooth is NOT seeing valid R/C input.")
        print("      Don't proceed — fix wiring or DIP1/DIP2 first.")
    elif "2 blink" in ans.lower() or "yellow blink" in ans.lower() or "red blink" in ans.lower():
        print("  ~~  STATUS 2 blinking can mean LiPo cell detection (normal first 5 s),")
        print("      low pack voltage, or over-temp.  If it persists, stop and check pack.")

    # ---- step 3: confirm neutral truly stops both motors ----
    print("\n[3/6] Neutral creep check")
    print("  Sending 'S' (1500 us on both channels) and holding 3 s.")
    print("  With autocal + deadband (DIP6 ON), BOTH treads should be DEAD STILL.")
    hold_signal(fd, "S", 1, hold_secs=3.0)
    ans = _prompt("Did either tread creep / spin / twitch?  (yes / no / which one)")
    if "yes" in ans.lower() or "left" in ans.lower() or "right" in ans.lower():
        print("  !!! Autocal didn't take, OR DIP6 is still OFF (microcontroller mode).")
        print("      - Re-check DIP6: it must be ON (UP) for Standard R/C with deadband.")
        print("      - Power-cycle the Sabertooth, keeping Arduino idling at 1500 us.")
        print("      - If still creeping: trim S1/S2 pot on the board (if present).")
    else:
        print("  ✓ Neutral is clean.")

    # ---- step 4: tiny forward — verifies S1/S2 wiring symmetry ----
    print("\n[4/6] Symmetric low-speed forward (5 taps each channel, ~3% forward)")
    print("  Both treads (if connected) should start moving the SAME way and at")
    print("  about the SAME speed.  If one moves and the other doesn't:")
    print("    - That side's wire (D10->S1 or D11->S2) is loose, OR")
    print("    - DIP4 got set to MIXED — in mixed mode this taps would only")
    print("      drive 'throttle' (S1), not steer (S2).  Fix: DIP4 must be OFF.")
    hold_signal(fd, "U", 5, hold_secs=3.0, label="ramp up 5 taps then hold...")
    ans = _prompt("Both treads turning the same direction at about the same speed?  "
                  "(yes / only-left / only-right / both-wrong-direction)")
    if "only" in ans.lower():
        print("  !!! Asymmetric response.  See the hints above — likely a wiring")
        print("      issue on the dead side, or DIP4 = Mixed by mistake.")
    elif "wrong" in ans.lower():
        print("  ~~  Both treads moving the wrong way means the Sabertooth's")
        print("      'forward' direction is opposite to the robot's.  Easy fix:")
        print("      swap each motor's M1A/M1B and M2A/M2B wires, OR invert in")
        print("      robot.ino by swapping the U/D logic.  Not a DIP issue.")
    else:
        print("  ✓ Symmetric forward.")
    print("  Returning to neutral...")
    hold_signal(fd, "S", 1, hold_secs=1.0)

    # ---- step 5: pivot test — confirms INDEPENDENT mode ----
    print("\n[5/6] Pivot test (left vs right) — verifies INDEPENDENT mode")
    print("  'L' nudges left tread + reverse, right tread + forward.  If you")
    print("  see ONE tread move opposite to the other, channels are independent.")
    print("  If both move the same way (or nothing happens), DIP4 is wrong.")
    hold_signal(fd, "L", 5, hold_secs=2.0, label="rotate-left taps then hold...")
    ans = _prompt("Did the two treads counter-rotate (one fwd, one rev)?  (yes/no)")
    if "no" in ans.lower():
        print("  !!! That means DIP4 is set to Mixed, or one channel isn't")
        print("      receiving signal.  In Mixed mode S2 is interpreted as")
        print("      'steering' and 'L'/'R' would do unexpected things.")
        print("      Fix: DIP4 = OFF (UP toward the legend's 'Independent' label).")
    else:
        print("  ✓ Independent channels working.")
    print("  Stopping...")
    hold_signal(fd, "S", 1, hold_secs=1.0)

    # ---- step 6: failsafe ----
    print("\n[6/6] Failsafe (Standard R/C mode, DIP6 ON)")
    print("  We can't easily simulate signal loss without unplugging the USB,")
    print("  but you can verify it: while the wifi-server is running and the")
    print("  motors are spinning, briefly pull the USB cable from the Pi end.")
    print("  Within ~0.2 s the motors should STOP on their own.  In micro-")
    print("  controller mode (DIP6 OFF) they would keep running indefinitely.")
    print("  Don't run that test now — just know it's there.")
    print()
    print("=" * 70)
    print(" Preflight complete.  Final neutral 'S' will be sent on exit.")
    print(" If everything above checked out, reconnect motor leads (if you")
    print(" had unscrewed them), and proceed to:")
    print("    ./scripts/tty_test.py --demo        # gentle level sweep")
    print("    ./bin/robot wifi-server             # keyboard control")
    print("=" * 70)


def _steps_to_pct(target_pct, reverse):
    """How many U (or D) commands to reach target_pct of full throw on both
    channels, starting from the current modeled level."""
    sign = +1 if reverse else -1
    want = max(0, min(180, NEUTRAL + sign * round(target_pct / 100.0 * NEUTRAL)))
    have = _level["L"]               # U/D keep both channels equal
    return abs(want - have) // STEP, ("D" if reverse else "U")


def run_demo(fd, hold=2.5):
    print("\n" + "=" * 64)
    print(" MOTOR LEVEL SWEEP — both treads, several speeds, then STOP")
    print(" >>> TREADS UP ON BLOCKS <<<   (Ctrl-C aborts; STOP is sent on exit)")
    print("=" * 64)
    print(" robot.ino only does ±3-per-tap nudges, so this taps many times to")
    print(" climb past the Sabertooth's R/C deadband to a real speed.\n")

    # make sure we start from a known neutral
    send(fd, "S")
    time.sleep(0.4)

    plan = [
        ("FORWARD", False, [25, 50, 75, 100]),
        ("REVERSE", True,  [25, 50, 75, 100]),
    ]
    for label, reverse, levels in plan:
        print(f"\n--- {label} ---")
        for pct in levels:
            n, key = _steps_to_pct(pct, reverse)
            print(f"  → ramp to ~{pct}% {label.lower()} ({n} x '{key}')")
            if n:
                send_n(fd, key, n)
            print(f"    holding {hold:.1f}s ...")
            for _ in range(int(hold * 10)):
                time.sleep(0.1)
                drain_rx(fd)
        print(f"  → STOP")
        send(fd, "S")
        time.sleep(0.8)
        drain_rx(fd)

    print("\nSweep done. Robot left in STOP.")
    print("If the treads never moved at any level: it's the Arduino→Sabertooth")
    print("side — D10→S1, D11→S2, Arduino GND→Sabertooth 0V, Sabertooth in")
    print("R/C+independent mode, battery on its terminals. Try `--send UUUUUUUUUU`")
    print("and probe D10/D11 for a ~1300us pulse.")


KEYMAP_BYTES = {
    b"w": "U", b"W": "U",
    b"s": "D", b"S": "D",
    b"a": "L", b"A": "L",
    b"d": "R", b"D": "R",
    b"u": "U", b"U": "U",      # also accept the raw firmware letters
    b"l": "L", b"r": "R",
    b" ": "S", b"\r": "S", b"\n": "S",
    b"\x1b[A": "U", b"\x1b[B": "D", b"\x1b[D": "L", b"\x1b[C": "R",  # arrow keys
    b"\x1bOA": "U", b"\x1bOB": "D", b"\x1bOD": "L", b"\x1bOC": "R",  # app-cursor arrows
}


def run_interactive(fd):
    print(__doc__.split("INTERACTIVE KEYS")[1].split("SAFETY")[0].rstrip())
    print("SAFETY: treads up on blocks the first time.\n")
    print("Listening for keys. (q or Esc to quit)\n")
    old = termios.tcgetattr(sys.stdin)
    try:
        tty.setraw(sys.stdin.fileno())
        while True:
            r, _, _ = select.select([sys.stdin, fd], [], [], 0.1)
            if fd in r:
                drain_rx(fd)
            if sys.stdin not in r:
                continue
            # read a small chunk so escape sequences arrive whole
            ch = os.read(sys.stdin.fileno(), 8)
            if not ch:
                continue
            if ch in (b"q", b"Q"):
                break
            if ch == b"\x1b":            # bare Esc (not an arrow sequence)
                break
            if ch in (b"?",):
                # re-print the key help; raw mode needs explicit CRs
                help_txt = __doc__.split("INTERACTIVE KEYS")[1].split("SAFETY")[0]
                sys.stdout.write(help_txt.replace("\n", "\r\n"))
                sys.stdout.flush()
                continue
            key = KEYMAP_BYTES.get(ch)
            if key is None and len(ch) == 1:
                key = KEYMAP_BYTES.get(ch.lower())
            if key is None:
                sys.stdout.write(f"  (ignored key {ch!r})\r\n")
                sys.stdout.flush()
                continue
            send(fd, key)
            # send() printed with '\n'; in raw mode we want CRLF
            sys.stdout.write("\r")
            sys.stdout.flush()
    finally:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old)
        print("\nLeaving interactive mode; sending STOP.")


def main():
    ap = argparse.ArgumentParser(
        description="Low-level tester for the Pi<->Arduino serial motor link.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    ap.add_argument("--list", action="store_true", help="list candidate serial ports and exit")
    ap.add_argument("--port", help="serial device (default: first /dev/ttyACM* or /dev/ttyUSB*)")
    ap.add_argument("--baud", type=int, default=115200, help="baud rate (default 115200)")
    ap.add_argument("--preflight", action="store_true",
                    help="interactive Sabertooth + Arduino dry-fire check.  Walks you through "
                    "powering the Sabertooth in the right order, watching the status LEDs, and "
                    "graduated low-speed pulses — catches DIP-switch and wiring mistakes before "
                    "any real driving.  Run with motor leads disconnected from the Sabertooth "
                    "(or treads up on blocks).")
    ap.add_argument("--demo", action="store_true",
                    help="motor level sweep: ramp both treads to ~25/50/75/100%% forward, hold each, "
                    "STOP, then the same in reverse, then STOP")
    ap.add_argument("--hold", type=float, default=2.5, metavar="SECS",
                    help="seconds to hold each speed in --demo (default 2.5)")
    ap.add_argument("--send", metavar="BYTES", help="send this string of U/D/L/R/S commands then exit "
                    "(spaces/commas = short pause); always ends with STOP")
    ap.add_argument("--interactive", action="store_true",
                    help="keyboard control (this is the default if neither --demo nor --send is given)")
    ap.add_argument("--no-reset-wait", action="store_true",
                    help="don't pause 2 s after opening the port (use if the board isn't an Uno-style "
                    "auto-reset board, or you just want a quick poke)")
    args = ap.parse_args()

    if args.list:
        list_ports()
        return

    port = args.port
    if not port:
        cands = candidate_ports()
        if not cands:
            sys.exit("No serial port found. Run with --list, or plug in the Arduino. "
                     "(`--port /dev/ttyACM0` to force one.)")
        port = cands[0]
        print(f"Auto-selected {port}  (override with --port; see all with --list)")

    fd = open_port(port, args.baud, reset_wait=not args.no_reset_wait)
    try:
        if args.send is not None:
            run_send_string(fd, args.send)
        elif args.preflight:
            run_preflight(fd)
        elif args.demo:
            run_demo(fd, hold=args.hold)
        else:
            run_interactive(fd)
    except KeyboardInterrupt:
        print("\nInterrupted.")
    finally:
        try:
            os.write(fd, b"S")     # always park the motors
            print("Sent final STOP ('S'). Closing port.")
        except OSError:
            pass
        os.close(fd)


if __name__ == "__main__":
    main()
