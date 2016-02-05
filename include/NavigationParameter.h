#ifndef NAVIGATIONPARAMETER_H
#define NAVIGATIONPARAMETER_H

enum DIRECTION
{
    UP,
    DOWN,
    LEFT,
    RIGHT,
    UNKNOWN
};

enum NetworkCommand
{
    REGISTER_CLIENT,
    REGISTER_ROBOT,
    NAVIGATE
};

struct NavigationParameter
{
    DIRECTION Direction;
};



#endif // NAVIGATIONPARAMETER_H
