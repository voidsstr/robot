#ifndef SERVERMANAGER_H
#define SERVERMANAGER_H

using boost::asio::ip::udp;

class ServerManager
{
    public:
        ServerManager(boost::asio::io_service& io_service, short port)

        virtual ~ServerManager();

        void StartRelay();
    protected:
    private:
};

#endif // SERVERMANAGER_H
