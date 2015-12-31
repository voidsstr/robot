#include <Servo.h> // include the Servo library
#include <ContinuousRotationServo.h>

// create the servo objects
ContinuousRotationServo Servo;

int accellerate = 0;
int decellerate = 0;

int accelleratePin = 7;
int decelleratePin = 6;

int level = 0;
boolean started = false;

void setup()
{
  Servo.begin(2);
  
  pinMode(accelleratePin, INPUT);
  pinMode(decelleratePin, INPUT);
}

void loop()
{
  accellerate = digitalRead(accelleratePin);
  decellerate = digitalRead(decelleratePin);
  
  if(started && level == 0)
  {
     started = false; 
  }
  
  if(accellerate == HIGH)
  {
    started = true;
    level += 1;
  }
  
  if(decellerate == HIGH)
  {
    started = true;
    level -= 1;
  }
  
  if(started)
  {
    Servo.rotate(level);
  }
}

