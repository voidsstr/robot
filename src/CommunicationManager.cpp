#include "CommunicationManager.h"

using boost::asio::ip::tcp;
using namespace boost::asio;

CommunicationManager::CommunicationManager()
{
    //ctor
}

CommunicationManager::~CommunicationManager()
{
    //dtor
}

CommunicationManager::Start()
{
    try
    {
        boost::asio::io_service io_service;

        tcp::resolver resolver(io_service);

        ip::tcp::endpoint tcp(
                ip::address::from_string(argv[0]),
                13
                );

        tcp::socket socket(io_service);
        socket.connect(tcp);

        for (;;)
        {
          boost::array<char, 128> buf;
          boost::system::error_code error;

          size_t len = socket.read_some(boost::asio::buffer(buf), error);

          if (error == boost::asio::error::eof)
            break; // Connection closed cleanly by peer.
          else if (error)
            throw boost::system::system_error(error); // Some other error.

          std::cout.write(buf.data(), len);
        }
    }
    catch (std::exception& e)
    {
        std::cerr << e.what() << std::endl;
    }
}
