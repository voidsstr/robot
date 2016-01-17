#include "CommunicationManager.h"

CommunicationManager::CommunicationManager()
{
    //ctor
}

CommunicationManager::~CommunicationManager()
{
    //dtor
}

void CommunicationManager::ListenForCommands()
{
    tcp::resolver::query query("10.0.0.24", "navigate"); //TODO: use addresses of server
    tcp::resolver resolver(io_service);
    tcp::resolver::iterator endpoint_iterator = resolver.resolve(query);

    tcp::socket socket(io_service);
    boost::asio::connect(socket, endpoint_iterator);
}
