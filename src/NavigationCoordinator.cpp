#include "NavigationCoordinator.h"

NavigationCoordinator::NavigationCoordinator()
{
    _rightWheelLevel = 0;
    _leftWheelLevel = 0;
}

NavigationCoordinator::~NavigationCoordinator()
{
    //dtor
    pwmWrite(RightWheelPin, 0);
    pwmWrite(LeftWheelPin, 0);
}

void NavigationCoordinator::UpdateNavigationParameters(NavigationParameter* navigationParameter)
{
    _updateMutex.lock();

    _pendingUpdates.push(navigationParameter);

    _updateMutex.unlock();
}

void NavigationCoordinator::MoveForward()
{
    //50-150 forward, 500-550 backwards
    if(_leftWheelLevel >= 150) {
        _leftWheelLevel = 150;
    }
    else {
        _leftWheelLevel += 10;
    }

    if(_rightWheelLevel >= 150) {
        _rightWheelLevel = 150;
    }
    else {
        _rightWheelLevel += 10;
    }
}

void NavigationCoordinator::ProcessUpdate()
{
    _updateMutex.lock();

    while(_pendingUpdates.size() > 0) {
        NavigationParameter* currentUpdate = _pendingUpdates.top();

        if(currentUpdate -> Direction == UP) {
            MoveForward();
        }
    }

    _updateMutex.unlock();
}

void NavigationCoordinator::Start()
{
	/*if(wiringPiSetup() == -1){ //when initialize wiring failed,print messageto screen
		printw("setup wiringPi failed !\n");
	}
    else {
        pinMode(RightWheelPin, PWM_OUTPUT);//pwm output mode
        pinMode(LeftWheelPin, PWM_OUTPUT);//pwm output mode

        //50-150 forward, 500-550 backwards

        while(1){

            if(_pendingUpdates.size() > 0) {
                ProcessUpdate();

                pwmWrite(RightWheelPin, _rightWheelLevel);
                pwmWrite(LeftWheelPin, _leftWheelLevel);
            }

            delay(100);
        }
    }*/
}
