#ifndef NAVIGATIONCOORDINATOR_H
#define NAVIGATIONCOORDINATOR_H

#include <wiringPi.h>
#include <stdio.h>

#include "InputProcessor.h"

#define RightWheelPin 0
#define LeftWheelPin 1

class NavigationCoordinator
{
    public:
        NavigationCoordinator();
        virtual ~NavigationCoordinator();
        void Start();
    protected:
    private:
        int _rightWheelLevel;
        int _leftWheelLevel;
        InputProcessor _inputProcessor;
};

#endif // NAVIGATIONCOORDINATOR_H
