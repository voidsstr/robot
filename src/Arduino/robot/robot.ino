#include <Servo.h> // include the Servo library

int isAccellerating = 0;
int isDecellerating = 0;

int isStopping = 0;

int isRotatingLeft = 0;
int isRotatingRight = 0;

int accelleratePin = 7;
int decelleratePin = 6;

int rotateLeftPin = 4;
int rotateRightPin = 5;

int stopPin = 2;

int rightMotorLevel = 90;
int leftMotorLevel = 90;

Servo* rightMotor = new Servo();
Servo* leftMotor = new Servo();

void setup()
{
  leftMotor->attach(10);
  rightMotor->attach(11);
  
  pinMode(accelleratePin, INPUT);
  pinMode(decelleratePin, INPUT);
  
  pinMode(rotateLeftPin, INPUT);
  pinMode(rotateRightPin, INPUT);
  pinMode(stopPin, INPUT);
}

void loop()
{
  delay(5);
  
  isAccellerating = digitalRead(accelleratePin);
  isDecellerating = digitalRead(decelleratePin);
  
  isRotatingLeft = digitalRead(rotateLeftPin);
  isRotatingRight = digitalRead(rotateRightPin);
  isStopping = digitalRead(stopPin);
  
  if(isStopping == HIGH)
  {
    stop();
  }
  else
  {
    if(isAccellerating == HIGH)
    {
      accellerate(rightMotor);
      accellerate(leftMotor);
    }
    else if(isDecellerating == HIGH)
    {
      decellerate(rightMotor);
      decellerate(leftMotor);
    }
    
    if(isRotatingLeft == HIGH)
    {
      accellerate(rightMotor);
      decellerate(leftMotor);
    }
    else if(isRotatingRight == HIGH)
    {
      accellerate(leftMotor);
      decellerate(rightMotor);
    }
    
    delay(1000);
    
    stop();
  }
}

void stop()
{
  leftMotor->write(90);
  rightMotor->write(90);
}

void accellerate(Servo* servo)
{
  int currentValue = servo->read();
  
  currentValue -= 3;
  
  servo->write(currentValue);
}

void decellerate(Servo* servo)
{
  int currentValue = servo->read();
  
  currentValue += 3;
  
  servo->write(currentValue);
}

