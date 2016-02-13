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
    else if(key == 260) {
        return DIRECTION::LEFT;
    }
    else if(key == 261) {
        return DIRECTION::RIGHT;
    }
    else if(key == 113) {
        throw std::invalid_argument("Quit!");
    }

    return DIRECTION::UNKNOWN;
}
