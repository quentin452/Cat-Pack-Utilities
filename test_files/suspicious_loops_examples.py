# Suspicious Loops Test Cases - Python
# This file contains various suspicious loop patterns for testing

import time
import threading
import requests
import sqlite3
import cv2
import numpy as np

class SuspiciousLoopsExamples:
    
    def __init__(self):
        self.data = []
    
    # Infinite loop - CRITICAL
    def infinite_loop(self):
        while True:
            data = [i for i in range(1000)]
            self.data.extend(data)
            # Missing break condition - CRITICAL ISSUE!
    
    # Infinite iterator loop - CRITICAL
    def infinite_iterator_loop(self):
        import itertools
        for i in itertools.count():
            result = i * i
            self.data.append(result)
            # No break condition - CRITICAL ISSUE!
    
    # Memory allocation in loop - HIGH RISK
    def memory_allocation_loop(self):
        for i in range(1000000):
            data_list = []  # New list each iteration
            data_dict = {}  # New dict each iteration
            data_set = set()  # New set each iteration
            data_list.append(i)
            data_dict[i] = str(i)
            data_set.add(i)
    
    # Triple nested loop - HIGH COMPLEXITY
    def triple_nested_loop(self):
        for i in range(100):
            for j in range(100):
                for k in range(100):
                    # O(n³) complexity - HIGH RISK!
                    result = i * j * k
                    print(f"{i},{j},{k} = {result}")
    
    # Quadruple nested loop - CRITICAL COMPLEXITY
    def quadruple_nested_loop(self):
        for i in range(50):
            for j in range(50):
                for k in range(50):
                    for l in range(50):
                        # O(n⁴) complexity - CRITICAL ISSUE!
                        self.process_data(i, j, k, l)
    
    # I/O operations in loop - HIGH RISK
    def io_in_loop(self):
        for i in range(1000):
            with open(f"temp_file_{i}.txt", "w") as f:
                print(f"Processing file {i}")  # I/O in loop
                f.write(f"Data {i}")
    
    # URL requests in loop - HIGH RISK
    def requests_in_loop(self):
        urls = ["http://example.com"] * 100
        for url in urls:
            response = requests.get(url)  # Network I/O in loop - HIGH RISK!
            print(f"Status: {response.status_code}")
    
    # Database operations in loop - HIGH RISK
    def database_in_loop(self):
        conn = sqlite3.connect("test.db")
        cursor = conn.cursor()
        
        for i in range(1000):
            cursor.execute("SELECT * FROM users WHERE id = ?", (i,))  # DB query in loop
            result = cursor.fetchone()
            print(f"User {i}: {result}")
        
        conn.close()
    
    # String concatenation in loop - MEDIUM RISK
    def string_concatenation_loop(self):
        result = ""
        for i in range(10000):
            result += f"Item {i}, "  # Inefficient string concatenation
        return result
    
    # Better approach with join
    def string_join_loop(self):
        items = []
        for i in range(10000):
            items.append(f"Item {i}")
        return ", ".join(items)
    
    # Inefficient collection operations in loop
    def inefficient_collection_loop(self):
        data_list = [f"Item {i}" for i in range(1000)]
        
        for item in data_list:
            if "Item 500" in data_list:  # O(n) operation in loop
                print("Found item 500")
    
    # Recursive calls in loop - HIGH RISK
    def recursive_calls_in_loop(self):
        for i in range(30):
            result = self.fibonacci(i)  # Recursive call in loop - HIGH RISK!
            print(f"Fibonacci({i}) = {result}")
    
    def fibonacci(self, n):
        if n <= 1:
            return n
        return self.fibonacci(n - 1) + self.fibonacci(n - 2)
    
    # Sleep in loop - MEDIUM RISK
    def sleep_in_loop(self):
        for i in range(10):
            time.sleep(1)  # Blocking operation in loop
            print(f"Iteration {i}")
    
    # Heavy library operations in loop - MEDIUM RISK
    def heavy_operations_loop(self):
        for i in range(100):
            # OpenCV operations in loop
            image = np.zeros((1000, 1000, 3), dtype=np.uint8)
            blurred = cv2.GaussianBlur(image, (15, 15), 0)
            
            # NumPy heavy operations
            large_array = np.random.rand(1000, 1000)
            result = np.linalg.inv(large_array)
    
    # Threading operations in loop - MEDIUM RISK
    def threading_in_loop(self):
        for i in range(100):
            thread = threading.Thread(target=self.worker_function, args=(i,))
            thread.start()
            # Not joining threads - potential resource leak
    
    def worker_function(self, data):
        time.sleep(0.1)
        print(f"Worker processing {data}")
    
    # Append operations without size limit - MEDIUM RISK
    def growing_collection_loop(self):
        for i in range(1000000):
            self.data.append(i)  # Collection grows indefinitely
            # No cleanup or size limits
    
    # Loop with proper exit condition (GOOD EXAMPLE)
    def proper_loop_with_break(self):
        while True:
            user_input = self.get_user_input()
            if user_input == "quit":
                break  # Proper exit condition
            self.process_input(user_input)
    
    # Loop with proper resource management (GOOD EXAMPLE)
    def proper_file_handling_loop(self):
        filenames = [f"file_{i}.txt" for i in range(10)]
        for filename in filenames:
            try:
                with open(filename, 'r') as f:  # Proper resource management
                    content = f.read()
                    self.process_content(content)
            except FileNotFoundError:
                print(f"File {filename} not found")
    
    # Helper methods
    def process_data(self, i, j, k, l):
        return i + j + k + l
    
    def get_user_input(self):
        return "test"
    
    def process_input(self, input_data):
        pass
    
    def process_content(self, content):
        pass

# Example of problematic global loop
global_data = []

def problematic_global_loop():
    while True:
        global_data.extend([i for i in range(1000)])
        # No break condition and grows global data indefinitely

if __name__ == "__main__":
    examples = SuspiciousLoopsExamples()
    
    # These would cause issues if run:
    # examples.infinite_loop()
    # examples.memory_allocation_loop()
    # examples.triple_nested_loop()
    
    print("Suspicious loops examples loaded (not executed to avoid issues)")