#!/bin/bash
# Compile and run Tile Matching Game

cd "$(dirname "$0")"   # always run from the script's own directory

echo "Compiling..."
javac Stack.java Queue.java PlayerScore.java TileMatchingGame.java
if [ $? -ne 0 ]; then
    echo "Compilation failed. Make sure Java (JDK) is installed."
    exit 1
fi

java TileMatchingGame
