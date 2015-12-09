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
    printw("Key pressed");

    if(key == 256) {
		return DIRECTION::UP;
    }

    return NULL;
}
