#include "NavigationCoordinator.h"

NavigationCoordinator::NavigationCoordinator()
{
    NavigationCoordinator::_rightWheelLevel = 0;
    NavigationCoordinator::_leftWheelLevel = 0;
}

NavigationCoordinator::~NavigationCoordinator()
{
    //dtor
}

void NavigationCoordinator::Start()
{
	if(wiringPiSetup() == -1){ //when initialize wiring failed,print messageto screen
		printf("setup wiringPi failed !\n");
	}
    else {
        pinMode(RightWheelPin, PWM_OUTPUT);//pwm output mode
        pinMode(LeftWheelPin, PWM_OUTPUT);//pwm output mode

        //50-150 forward, 500-550 backwards

        while(1){
            pwmWrite(RightWheelPin, NavigationCoordinator::_rightWheelLevel);
            pwmWrite(LeftWheelPin, NavigationCoordinator::_leftWheelLevel);

            _inputProcessor.ProcessInput();
        }
    }
}
