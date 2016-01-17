#ifndef COMMUNICATIONMANAGER_H
#define COMMUNICATIONMANAGER_H

#include <iostream>
#include <boost/array.hpp>
#include <boost/asio.hpp>

using boost::asio::ip::tcp;

class CommunicationManager
{
    public:
        CommunicationManager();
        virtual ~CommunicationManager();
        void ListenForCommands();
    protected:
    private:
        boost::asio::io_service io_service;
        tcp::resolver resolver(io_service);
};

#endif // COMMUNICATIONMANAGER_H
