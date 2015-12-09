#ifndef INPUTPROCESSOR_H
#define INPUTPROCESSOR_H

#include <curses.h>

#include "NavigationParameter.h"

class InputProcessor
{
    public:
        InputProcessor();
        virtual ~InputProcessor();
        DIRECTION ProcessInput(int key);
    protected:
    private:
};

#endif // INPUTPROCESSOR_H
