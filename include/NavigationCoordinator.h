#ifndef NAVIGATIONCOORDINATOR_H
#define NAVIGATIONCOORDINATOR_H

#include <wiringPi.h>
#include <stdio.h>
#include <mutex>
#include <stack>

#include "InputProcessor.h"
#include "NavigationParameter.h"

#define RightWheelPin 1
#define LeftWheelPin 0

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
        int _rightWheelLevel;
        int _leftWheelLevel;
        std::stack<DIRECTION> _pendingUpdates;
        void MoveForward();
        void MoveBackward();
};

#endif // NAVIGATIONCOORDINATOR_H
