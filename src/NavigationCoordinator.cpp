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

void NavigationCoordinator::UpdateNavigationParameters(DIRECTION navigationParameter)
{
    _pendingUpdates.push(navigationParameter);
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

void NavigationCoordinator::MoveBackward()
{
    //50-150 forward, 500-550 backwards
    if(_leftWheelLevel <= 0) {
        _leftWheelLevel = 0;
    }
    else {
        _leftWheelLevel -= 10;
    }

    if(_rightWheelLevel <= 0) {
        _rightWheelLevel = 0;
    }
    else {
        _rightWheelLevel -= 10;
    }
}

void NavigationCoordinator::ProcessUpdate()
{
    if(_pendingUpdates.size() > 0) {
        while(_pendingUpdates.size() > 0) {
            DIRECTION currentUpdate = _pendingUpdates.top();

            if(currentUpdate == DIRECTION::UP) {
                MoveForward();
            }
            else if(currentUpdate == DIRECTION::DOWN) {
                MoveForward();
            }
        }

        pwmWrite(RightWheelPin, _rightWheelLevel);
        pwmWrite(LeftWheelPin, _leftWheelLevel);
    }
}

void NavigationCoordinator::Start()
{
	if(wiringPiSetup() == -1){ //when initialize wiring failed,print messageto screen
		printw("setup wiringPi failed !\n");
	}
    else {
        pinMode(RightWheelPin, PWM_OUTPUT);//pwm output mode
        pinMode(LeftWheelPin, PWM_OUTPUT);//pwm output mode
    }
}
