#ifndef NAVIGATIONCOORDINATOR_H
#define NAVIGATIONCOORDINATOR_H

#include <wiringPi.h>
#include <stdio.h>
#include <stdlib.h>
#include <mutex>
#include <stack>

#include "InputProcessor.h"
#include "NavigationParameter.h"

#define AcceleratePin 1
#define DecelleratePin 0
#define RotateRightPin 3
#define RotateLeftPin 4
#define StopPin 5

#define LeftWheelPin 0
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
