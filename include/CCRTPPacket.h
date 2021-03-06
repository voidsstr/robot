#ifndef CCRTPPACKET_H
#define CCRTPPACKET_H


// System
#include <cstring>


/*! \brief Class to hold and process communication-related data for
  the CRTProtocol */
class CCRTPPacket {
 private:
  // Variables
  /*! \brief Internal storage pointer for payload data inside the
    packet
    This data is freed when either new data is set or the class
    instance is destroyed.*/
  char *m_cData;
  /*! \brief The length of the data pointed to by m_cData */
  int m_nDataLength;
  /*! \brief The copter port the packet will be delivered to */
  int m_nPort;
  /*! \brief The copter channel the packet will be delivered to */
  int m_nChannel;
  bool m_bIsPingPacket;

  // Functions
  /*! \brief Sets all internal variables to their default values.
    The function clearData() should be called before this if it is
    used outside of the constructor. */
  void basicSetup();
  /*! \brief Deletes the internally stored data and resets the data
    length and the pointer to zero */
  void clearData();

 public:
  /*! \brief Constructor for the CCRTPPacket communication packet
    class
    Initializes the communication packet and sets the given
    channel. The packet starts out without payload data.
    \param nChannel The channel the payload in this packet is
    designated for. */
  CCRTPPacket(int nChannel);
  /*! \brief Convenience constructor for the CCRTPPacket communication
    packet class
    Initializes the communication packet and sets the given
    channel. The given data is set as the internal payload data.
    \param cData The data pointer to read the new payload data from
    \param nDataLength The length (in bytes) of data to read from
    cData
    \param nChannel The channel the payload in this packet is
    designated for. */
  CCRTPPacket(char *cData, int nDataLength, int nChannel);
  CCRTPPacket(char cData, int nPort);
  /*! \brief Destructor for the packet class
    De-initializes the packet and deletes all available payload data
    stored. */
  ~CCRTPPacket();

  /*! \brief Copies the given data of the specified length to the
    internal storage.
    \param cData Pointer pointing to the data that should be used as
    payload
    \param nDataLength Length (in bytes) of the data that should be
    read from cData for storage */
  void setData(char *cData, int nDataLength);
  /*! \brief Gives out the pointer to the internally stored data
    Don't manipulate the data pointed to by this pointer. Usually, you
    won't have to call this function at all as it is used by the more
    interface-designated functions.
    \return Returns a direct pointer to the internally stored data */
  char *data();
  /*! \brief Returns the length of the currently stored data (in
      bytes)
    \return Returns the number of bytes stored as payload data */
  int dataLength();


  /*! \brief Set the copter port to send the payload data to
    The port identifies the purpose of the packet on the copter. This
    function sets the port that is later used in sendableData().
    \param nPort Port number to set */
  void setPort(int nPort);
  /*! \brief Returns the currently set port number */
  int port();

  /*! \brief Set the copter channel to send the payload data to
    The channel identifies the purpose of the packet on the
    copter. This function sets the channel that is later used in
    sendableData().
    \param nChannel Channel number to set */
  void setChannel(int nChannel);
  /*! \brief Returns the currently set channel number */
  int channel();

  void setIsPingPacket(bool bIsPingPacket);
  bool isPingPacket();
};

#endif // CCRTPPACKET_H
