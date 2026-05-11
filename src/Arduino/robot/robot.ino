#include <Servo.h>

/*
 * Robot Motor Controller - USB Serial Input Version
 *
 * Receives single-character commands from the Raspberry Pi over the USB
 * cable (the same cable used to flash the board). On an Uno/Nano/Leonardo
 * this is just the built-in `Serial` object — no extra wiring, no level
 * shifting. Drives a Sabertooth dual motor driver in R/C mode, treating the
 * two channels as tank treads.
 *
 * WIRING — Pi ↔ Arduino:
 *   A single USB A-to-B (Uno) / micro-USB (Nano/Leonardo) cable from the Pi
 *   to the Arduino carries power, programming and the command link. The
 *   Arduino enumerates on the Pi as /dev/ttyACM0 (or /dev/ttyUSB0 on CH340
 *   clones). Nothing else is needed between the two boards.
 *
 * WIRING — Arduino → Sabertooth motor driver (R/C input mode):
 *   D10            → Sabertooth S1   (left tread channel)
 *   D11            → Sabertooth S2   (right tread channel)
 *   Arduino GND    → Sabertooth 0V   (signal ground next to S1/S2 — required)
 *   Set the Sabertooth DIP switches for R/C mode, independent (un-mixed)
 *   channels. Battery/motor wiring is on the Sabertooth's own terminals;
 *   do NOT power motors from the Arduino.
 *
 * Servo pulse → motor mapping (Sabertooth R/C):
 *   write(90)  ≈ 1500 us → stop
 *   write(0)   ≈ 1000 us → full reverse
 *   write(180) ≈ 2000 us → full forward
 *
 * PROTOCOL (115200 baud, 8N1):
 *   'U' = accelerate (forward)
 *   'D' = decelerate (backward)
 *   'L' = rotate left
 *   'R' = rotate right
 *   'S' = stop (both motors to neutral)
 *   All other bytes are ignored.
 */

// Servo-signal output pins — wired to the Sabertooth R/C inputs S1 and S2.
const int leftMotorPin  = 10;  // → Sabertooth S1
const int rightMotorPin = 11;  // → Sabertooth S2

// Motor control values: 90 = stopped, <90 = forward, >90 = reverse.
int leftMotorLevel  = 90;
int rightMotorLevel = 90;

Servo leftMotor;
Servo rightMotor;

void setup()
{
    // USB serial doubles as the command link. No startup text —
    // the Pi does not expect noise on this channel.
    Serial.begin(115200);

    leftMotor.attach(leftMotorPin);
    rightMotor.attach(rightMotorPin);

    leftMotor.write(90);
    rightMotor.write(90);
}

void loop()
{
    while (Serial.available() > 0)
    {
        char cmd = (char)Serial.read();

        switch (cmd)
        {
            case 'U': accelerate();  break;
            case 'D': decelerate();  break;
            case 'L': rotateLeft();  break;
            case 'R': rotateRight(); break;
            case 'S': stopMotors();  break;
            default: /* ignore */    break;
        }

        leftMotor.write(leftMotorLevel);
        rightMotor.write(rightMotorLevel);
    }
}

void accelerate()
{
    leftMotorLevel  = constrain(leftMotorLevel  - 3, 0, 180);
    rightMotorLevel = constrain(rightMotorLevel - 3, 0, 180);
}

void decelerate()
{
    leftMotorLevel  = constrain(leftMotorLevel  + 3, 0, 180);
    rightMotorLevel = constrain(rightMotorLevel + 3, 0, 180);
}

void rotateLeft()
{
    rightMotorLevel = constrain(rightMotorLevel - 3, 0, 180);
    leftMotorLevel  = constrain(leftMotorLevel  + 3, 0, 180);
}

void rotateRight()
{
    leftMotorLevel  = constrain(leftMotorLevel  - 3, 0, 180);
    rightMotorLevel = constrain(rightMotorLevel + 3, 0, 180);
}

void stopMotors()
{
    leftMotorLevel  = 90;
    rightMotorLevel = 90;
}
