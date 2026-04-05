/**
 * Generic Stack implementation using a fixed-size array internally.
 * Supports only: push, pop, peek, isFull, isEmpty, size.
 */
public class Stack<T> {

    private Object[] elements;
    private int top;
    private int capacity;

    /**
     * Constructs a Stack with the given maximum capacity.
     *
     * @param capacity the maximum number of elements this stack can hold
     */
    public Stack(int capacity) {
        this.capacity = capacity;
        this.elements = new Object[capacity];
        this.top = -1;
    }

    /**
     * Pushes an item onto the top of the stack.
     *
     * @param item the item to push
     * @throws RuntimeException if the stack is already full
     */
    public void push(T item) {
        if (isFull()) {
            throw new RuntimeException("Stack overflow: cannot push to a full stack.");
        }
        elements[++top] = item;
    }

    /**
     * Removes and returns the item at the top of the stack.
     *
     * @return the top item
     * @throws RuntimeException if the stack is empty
     */
    @SuppressWarnings("unchecked")
    public T pop() {
        if (isEmpty()) {
            throw new RuntimeException("Stack underflow: cannot pop from an empty stack.");
        }
        T item = (T) elements[top];
        elements[top--] = null; // help GC
        return item;
    }

    /**
     * Returns (without removing) the item at the top of the stack.
     *
     * @return the top item
     * @throws RuntimeException if the stack is empty
     */
    @SuppressWarnings("unchecked")
    public T peek() {
        if (isEmpty()) {
            throw new RuntimeException("Stack is empty: cannot peek.");
        }
        return (T) elements[top];
    }

    /**
     * Returns true if the stack has reached its maximum capacity.
     */
    public boolean isFull() {
        return top == capacity - 1;
    }

    /**
     * Returns true if the stack contains no elements.
     */
    public boolean isEmpty() {
        return top == -1;
    }

    /**
     * Returns the number of elements currently in the stack.
     */
    public int size() {
        return top + 1;
    }
}
