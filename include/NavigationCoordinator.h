#ifndef NAVIGATIONCOORDINATOR_H
#define NAVIGATIONCOORDINATOR_H

#include <stdio.h>
#include <stdlib.h>
#include <mutex>
#include <queue>
#include <string>

#include "InputProcessor.h"
#include "NavigationParameter.h"

// Serial link to the Arduino motor controller, over the USB cable.
// The Arduino enumerates as /dev/ttyACM0 (Uno/Leonardo) or /dev/ttyUSB0
// (CH340 clones). No GPIO wiring, no level shifting — just the USB cable.
// Note: opening this port toggles DTR, which resets most Arduino boards,
// so Start() waits for the bootloader before sending anything.
#define ARDUINO_SERIAL_PORT "/dev/ttyACM0"
#define ARDUINO_SERIAL_BAUD 115200

// Single-character commands sent over the serial link.
#define CMD_ACCELERATE   'U'
#define CMD_DECELERATE   'D'
#define CMD_ROTATE_LEFT  'L'
#define CMD_ROTATE_RIGHT 'R'
#define CMD_STOP         'S'

#define MOVEMENT_INCREMENT 1

class NavigationCoordinator
{
public:
    NavigationCoordinator();
    virtual ~NavigationCoordinator();
    // Open the USB serial link to the Arduino. Defaults to ARDUINO_SERIAL_PORT
    // (/dev/ttyACM0); pass an explicit path for CH340 boards (/dev/ttyUSB0) etc.
    // Returns true if the port was opened successfully.
    bool Start(const std::string& port = ARDUINO_SERIAL_PORT);
    void UpdateNavigationParameters(DIRECTION navigationParameter);
    void ProcessUpdate();
    void StopRobot();

    void PrintTelemetry();

    bool IsConnected() const { return _serialFd >= 0; }
    bool IsMovingForward();
    bool IsMovingBackward();

    // Pi-side mirror of the Arduino's servo state (robot.ino has no echo,
    // so we track what we sent and predict what the board did with it).
    // Useful for status displays and "did my command land?" sanity checks.
    int LeftLevel()  const { return _leftLevel; }
    int RightLevel() const { return _rightLevel; }

protected:
private:
    std::queue<DIRECTION> _pendingUpdates;
    std::mutex _pendingMutex;
    void Accelerate();
    void Decelerate();
    void RotateLeft();
    void RotateRight();
    void SendCommand(char cmd);

    int _navigationCount;
    int _serialFd;

    // Mirrors of leftMotorLevel / rightMotorLevel in robot.ino.  90 = neutral
    // (≈1500 µs), 0 = full forward (≈1000 µs), 180 = full reverse (≈2000 µs).
    // Each Accelerate/Decelerate/Rotate* call updates these in lock-step with
    // the Arduino's constrain(±3, 0, 180) so the Pi can show motor state
    // without needing the Arduino to echo anything back.
    int _leftLevel  = 90;
    int _rightLevel = 90;
};

#endif // NAVIGATIONCOORDINATOR_H
