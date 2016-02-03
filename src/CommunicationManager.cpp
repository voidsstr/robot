#include "CommunicationManager.h"

CommunicationManager::CommunicationManager()
{

}

CommunicationManager::~CommunicationManager()
{
    //dtor
}

/*Main communication loop*/
void ListenToNetworkCommands(char* ipAddress, char* port, CommunicationManager* communicationManager, NavigationCoordinator* navigationCoordinator, InputProcessor* inputProcessor)
{
    boost::asio::io_service io_service;

    tcp::socket s(io_service);
    tcp::resolver resolver(io_service);

    //Connect to server
    boost::asio::connect(s, resolver.resolve({ipAddress, port}));

    char reply[1024];

    for(;;)
    {
        //Read data
        int buffer[1];
        boost::system::error_code error;

        size_t len = s.read_some(boost::asio::buffer(buffer, sizeof(buffer)), error);

        if (error == boost::asio::error::eof)
            break; // Connection closed cleanly by peer.
        else if (error)
            throw boost::system::system_error(error); // Some other error.

        DIRECTION input = inputProcessor->ProcessInput(buffer[0]);

        if(input != DIRECTION::UNKNOWN)
        {
            //Directly control robot
            navigationCoordinator->UpdateNavigationParameters(input);
            navigationCoordinator->ProcessUpdate();
        }
    }
}

void CommunicationManager::SendMessage(int message)
{
    boost::system::error_code ignored_error;
    boost::asio::write(*_socket, boost::asio::buffer(&message, sizeof(message)),
        boost::asio::transfer_all(), ignored_error);
}

void CommunicationManager::Connect(char* ipAddress, char* port, NavigationCoordinator* navigationCoordinator, InputProcessor* inputProcessor)
{
    std::thread(ListenToNetworkCommands, ipAddress, port, this, navigationCoordinator, inputProcessor).detach();
}

void CommunicationManager::StartListening()
{
    boost::asio::io_service io_service;

    tcp::acceptor a(io_service, tcp::endpoint(tcp::v4(), 1337));

    _socket = new tcp::socket(io_service);

    a.accept(*_socket);
}


