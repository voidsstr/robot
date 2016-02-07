#ifndef CLIENTMANAGER_H
#define CLIENTMANAGER_H


class ClientManager
{
    public:
        ClientManager(int port);
        virtual ~ClientManager();

        void SendMessage(int message);
    protected:
    private:
};

#endif // CLIENTMANAGER_H
