/**
 * Represents a single entry in the High Score Table.
 * Stores a player name and their associated score.
 */
public class PlayerScore {

    private String name;
    private int score;

    /**
     * Constructs a PlayerScore with the given name and score.
     *
     * @param name  the player's name
     * @param score the player's score
     */
    public PlayerScore(String name, int score) {
        this.name = name;
        this.score = score;
    }

    /** Returns the player's name. */
    public String getName() {
        return name;
    }

    /** Returns the player's score. */
    public int getScore() {
        return score;
    }

    /** Returns a formatted string representation "Name Score". */
    @Override
    public String toString() {
        return name + " " + score;
    }
}
