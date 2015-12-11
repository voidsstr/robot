#include "InputProcessor.h"

InputProcessor::InputProcessor()
{
    //ctor
}

InputProcessor::~InputProcessor()
{
    //dtor
}

DIRECTION InputProcessor::ProcessInput(int key)
{
    if(key == 259) {
		return DIRECTION::UP;
    }
    else if(key == 258) {
        return DIRECTION::DOWN;
    }

    return DIRECTION::UNKNOWN;
}
