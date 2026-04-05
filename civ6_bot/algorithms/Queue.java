/**
 * Generic circular Queue implementation using a fixed-size array internally.
 * Supports only: enqueue, dequeue, peek, isFull, isEmpty, size.
 */
public class Queue<T> {

    private Object[] elements;
    private int front;
    private int rear;
    private int size;
    private int capacity;

    /**
     * Constructs a Queue with the given maximum capacity.
     *
     * @param capacity the maximum number of elements this queue can hold
     */
    public Queue(int capacity) {
        this.capacity = capacity;
        this.elements = new Object[capacity];
        this.front = 0;
        this.rear = -1;
        this.size = 0;
    }

    /**
     * Adds an item to the rear of the queue.
     *
     * @param item the item to enqueue
     * @throws RuntimeException if the queue is already full
     */
    public void enqueue(T item) {
        if (isFull()) {
            throw new RuntimeException("Queue overflow: cannot enqueue to a full queue.");
        }
        rear = (rear + 1) % capacity;
        elements[rear] = item;
        size++;
    }

    /**
     * Removes and returns the item at the front of the queue.
     *
     * @return the front item
     * @throws RuntimeException if the queue is empty
     */
    @SuppressWarnings("unchecked")
    public T dequeue() {
        if (isEmpty()) {
            throw new RuntimeException("Queue underflow: cannot dequeue from an empty queue.");
        }
        T item = (T) elements[front];
        elements[front] = null; // help GC
        front = (front + 1) % capacity;
        size--;
        return item;
    }

    /**
     * Returns (without removing) the item at the front of the queue.
     *
     * @return the front item
     * @throws RuntimeException if the queue is empty
     */
    @SuppressWarnings("unchecked")
    public T peek() {
        if (isEmpty()) {
            throw new RuntimeException("Queue is empty: cannot peek.");
        }
        return (T) elements[front];
    }

    /**
     * Returns true if the queue has reached its maximum capacity.
     */
    public boolean isFull() {
        return size == capacity;
    }

    /**
     * Returns true if the queue contains no elements.
     */
    public boolean isEmpty() {
        return size == 0;
    }

    /**
     * Returns the number of elements currently in the queue.
     */
    public int size() {
        return size;
    }
}
