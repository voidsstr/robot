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

int NavigationCoordinator::Accelerate(int pwmValue)
{
    //50-150 forward, 500-550 backwards
    if(pwmValue >= 150) {
        pwmValue = 150;
    }
    else if(pwmValue == 500) {
        pwmValue = 0;
    }
    else if(pwmValue > 500) {
        pwmValue -= MOVEMENT_INCREMENT;
    }
    else {
        pwmValue += MOVEMENT_INCREMENT;
    }

    return pwmValue;
}

int NavigationCoordinator::Decelerate(int pwmValue)
{
    if(pwmValue >= 0 && pwmValue <= 150) {
        pwmValue;
    }
    else if(pwmValue == 0) {
        pwmValue = 500;
    }
    else {
        pwmValue += MOVEMENT_INCREMENT;
    }

    return pwmValue;
}

void NavigationCoordinator::ProcessUpdate()
{
    if(_pendingUpdates.size() > 0) {
        while(_pendingUpdates.size() > 0) {
            DIRECTION currentUpdate = _pendingUpdates.top();
            _pendingUpdates.pop();

            if(currentUpdate == DIRECTION::UP) {
                _leftWheelLevel = Accelerate(_leftWheelLevel);
                _rightWheelLevel = Accelerate(_rightWheelLevel);
            }
            else if(currentUpdate == DIRECTION::DOWN) {
                _leftWheelLevel = Decelerate(_leftWheelLevel);
                _rightWheelLevel = Decelerate(_rightWheelLevel);
            }
            else if(currentUpdate == DIRECTION::LEFT) {
                _leftWheelLevel = Decelerate(_leftWheelLevel);
                _rightWheelLevel = Accelerate(_rightWheelLevel);
            }
            else if(currentUpdate == DIRECTION::RIGHT) {
                _leftWheelLevel = Accelerate(_leftWheelLevel);
                _rightWheelLevel = Decelerate(_rightWheelLevel);
            }
        }


        pwmWrite(RightWheelPin, 550);
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
