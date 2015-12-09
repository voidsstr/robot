#ifndef NAVIGATIONCOORDINATOR_H
#define NAVIGATIONCOORDINATOR_H

#include <wiringPi.h>
#include <stdio.h>
#include <mutex>
#include <stack>

#include "InputProcessor.h"
#include "NavigationParameter.h"

#define RightWheelPin 0
#define LeftWheelPin 1

class NavigationCoordinator
{
    public:
        NavigationCoordinator();
        virtual ~NavigationCoordinator();
        void Start();
        void UpdateNavigationParameters(NavigationParameter* navigationParameter);
    protected:
    private:
        void ProcessUpdate();
        int _rightWheelLevel;
        int _leftWheelLevel;
        std::stack<NavigationParameter*> _pendingUpdates;
        std::mutex _updateMutex;
        void MoveForward();
};

#endif // NAVIGATIONCOORDINATOR_H
