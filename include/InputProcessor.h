#ifndef INPUTPROCESSOR_H
#define INPUTPROCESSOR_H


class InputProcessor
{
    public:
        InputProcessor();
        virtual ~InputProcessor();
        void ProcessInput();
    protected:
    private:
};

struct NavigationAdjustment
{
    int Navigation[4];
};

#endif // INPUTPROCESSOR_H
