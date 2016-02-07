#ifndef CONTROLLER_H
#define CONTROLLER_H


class Controller
{
    public:
        Controller();
        virtual ~Controller();

        void SendCommand(int command);
    protected:
    private:
};

#endif // CONTROLLER_H
