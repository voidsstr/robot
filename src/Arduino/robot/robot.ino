#include <Servo.h>

/*
 * Robot Motor Controller - GPIO Input Version
 *
 * Receives control signals from Raspberry Pi via GPIO pins.
 * Each command is a 10ms HIGH pulse on the corresponding pin.
 *
 * WIRING:
 *   Raspberry Pi (BCM)  →  Arduino Pin  →  Function
 *   ─────────────────────────────────────────────────
 *   GPIO 17 (Pin 11)    →  D7           →  Accelerate
 *   GPIO 18 (Pin 12)    →  D6           →  Decelerate
 *   GPIO 27 (Pin 13)    →  D5           →  Rotate Right
 *   GPIO 22 (Pin 15)    →  D4           →  Rotate Left
 *   GPIO 23 (Pin 16)    →  D2           →  Stop
 *   GND (Pin 6,9,etc)   →  GND          →  Common Ground
 *
 * SERVO WIRING:
 *   Left Motor Signal   →  D10
 *   Right Motor Signal  →  D11
 *   Servo Power         →  External 5V (NOT from Arduino!)
 *   Servo GND           →  Common GND
 */

// GPIO Input pins (directly from Raspberry Pi)
const int acceleratePin = 7;
const int deceleratePin = 6;
const int rotateRightPin = 5;
const int rotateLeftPin = 4;
const int stopPin = 2;

// Servo output pins
const int leftMotorPin = 10;
const int rightMotorPin = 11;

// Motor control values (90 = stopped, <90 = forward, >90 = backward)
int leftMotorLevel = 90;
int rightMotorLevel = 90;

// Servo objects
Servo leftMotor;
Servo rightMotor;

// State tracking
int isAccelerating = 0;
int isDecelerating = 0;
int isRotatingLeft = 0;
int isRotatingRight = 0;
int isStopping = 0;

void setup()
{
    // Initialize serial for debugging (optional)
    Serial.begin(115200);
    Serial.println("Robot Motor Controller Starting...");

    // Attach servos
    leftMotor.attach(leftMotorPin);
    rightMotor.attach(rightMotorPin);

    // Start in stopped position
    leftMotor.write(90);
    rightMotor.write(90);

    // Configure GPIO input pins with pull-down (external or internal)
    pinMode(acceleratePin, INPUT);
    pinMode(deceleratePin, INPUT);
    pinMode(rotateRightPin, INPUT);
    pinMode(rotateLeftPin, INPUT);
    pinMode(stopPin, INPUT);

    Serial.println("Ready - Waiting for GPIO signals...");
}

void loop()
{
    // Read all input pins
    isAccelerating = digitalRead(acceleratePin);
    isDecelerating = digitalRead(deceleratePin);
    isRotatingLeft = digitalRead(rotateLeftPin);
    isRotatingRight = digitalRead(rotateRightPin);
    isStopping = digitalRead(stopPin);

    // Process commands (priority: stop > accelerate/decelerate > rotate)
    if (isStopping == HIGH)
    {
        stopMotors();
        Serial.println("STOP");
    }
    else
    {
        if (isAccelerating == HIGH)
        {
            accelerate();
            Serial.println("ACCELERATE");
        }
        else if (isDecelerating == HIGH)
        {
            decelerate();
            Serial.println("DECELERATE");
        }

        if (isRotatingLeft == HIGH)
        {
            rotateLeft();
            Serial.println("ROTATE LEFT");
        }
        else if (isRotatingRight == HIGH)
        {
            rotateRight();
            Serial.println("ROTATE RIGHT");
        }
    }

    // Write current values to servos
    leftMotor.write(leftMotorLevel);
    rightMotor.write(rightMotorLevel);

    // Small delay for stability
    delay(5);
}

void accelerate()
{
    // Decrease values to go forward (towards 0)
    leftMotorLevel = constrain(leftMotorLevel - 3, 0, 180);
    rightMotorLevel = constrain(rightMotorLevel - 3, 0, 180);
}

void decelerate()
{
    // Increase values to go backward (towards 180)
    leftMotorLevel = constrain(leftMotorLevel + 3, 0, 180);
    rightMotorLevel = constrain(rightMotorLevel + 3, 0, 180);
}

void rotateLeft()
{
    // Right motor forward, left motor backward
    rightMotorLevel = constrain(rightMotorLevel - 3, 0, 180);
    leftMotorLevel = constrain(leftMotorLevel + 3, 0, 180);
}

void rotateRight()
{
    // Left motor forward, right motor backward
    leftMotorLevel = constrain(leftMotorLevel - 3, 0, 180);
    rightMotorLevel = constrain(rightMotorLevel + 3, 0, 180);
}

void stopMotors()
{
    leftMotorLevel = 90;
    rightMotorLevel = 90;
}
