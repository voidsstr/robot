#ifndef NAVIGATIONCOORDINATOR_H
#define NAVIGATIONCOORDINATOR_H

#include <wiringPi.h>
#include <stdio.h>
#include <stdlib.h>
#include <mutex>
#include <stack>

#include "InputProcessor.h"
#include "NavigationParameter.h"

#define RightWheelPin 1
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
    protected:
    private:
        std::stack<DIRECTION> _pendingUpdates;
        int Accelerate();
        int Decelerate();
};

#endif // NAVIGATIONCOORDINATOR_H
