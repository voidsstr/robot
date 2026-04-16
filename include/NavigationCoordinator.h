#ifndef NAVIGATIONCOORDINATOR_H
#define NAVIGATIONCOORDINATOR_H

#include <wiringPi.h>
#include <stdio.h>
#include <stdlib.h>
#include <mutex>
#include <stack>

#include "InputProcessor.h"
#include "NavigationParameter.h"

// WiringPi pin numbers (BCM GPIO numbers in comments)
// These connect directly to Arduino digital input pins
#define AcceleratePin   0   // WiringPi 0 = BCM GPIO 17 → Arduino D7
#define DecelleratePin  1   // WiringPi 1 = BCM GPIO 18 → Arduino D6
#define RotateRightPin  2   // WiringPi 2 = BCM GPIO 27 → Arduino D5
#define RotateLeftPin   3   // WiringPi 3 = BCM GPIO 22 → Arduino D4
#define StopPin         4   // WiringPi 4 = BCM GPIO 23 → Arduino D2

#define MOVEMENT_INCREMENT 1

class NavigationCoordinator
{
public:
    NavigationCoordinator();
    virtual ~NavigationCoordinator();
    void Start();
    void UpdateNavigationParameters(DIRECTION navigationParameter);
    void ProcessUpdate();
    void StopRobot();

    void PrintTelemetry();

    bool IsMovingForward();
    bool IsMovingBackward();

protected:
private:
    std::stack<DIRECTION> _pendingUpdates;
    void Accelerate();
    void Decelerate();
    void RotateLeft();
    void RotateRight();
    void NotifyPin(int pin);

    int _navigationCount;
};

#endif // NAVIGATIONCOORDINATOR_H
