# Test Python file with various security vulnerabilities

import os
import sqlite3
import subprocess
import pickle
import random
import hashlib

class VulnerablePython:
    
    # HIGH: Command injection
    def execute_command(self, user_input):
        os.system("ping " + user_input)  # Command injection
        subprocess.call("ls " + user_input, shell=True)  # Shell injection
    
    # HIGH: SQL injection
    def get_user_data(self, user_id):
        conn = sqlite3.connect('database.db')
        cursor = conn.cursor()
        query = "SELECT * FROM users WHERE id = " + user_id  # SQL injection
        cursor.execute(query)
        return cursor.fetchall()
    
    # CRITICAL: Insecure deserialization
    def load_data(self, data):
        return pickle.loads(data)  # Dangerous deserialization
    
    # HIGH: Path traversal
    def read_file(self, filename):
        with open("/uploads/" + filename, 'r') as f:  # Path traversal
            return f.read()
    
    # HIGH: Hardcoded credentials
    PASSWORD = "secret123"  # Hardcoded password
    API_KEY = "abc123456789secret"  # Hardcoded API key
    
    # MEDIUM: Weak cryptography
    def hash_password(self, password):
        return hashlib.md5(password.encode()).hexdigest()  # Weak hash
    
    # MEDIUM: Weak random
    def generate_token(self):
        return random.randint(1000, 9999)  # Weak random
    
    # MEDIUM: Eval injection
    def calculate(self, expression):
        return eval(expression)  # Code injection
    
    # Example of secure code (should not be flagged)
    def secure_function(self):
        print("This is secure code")
        return True

# JavaScript-like patterns in comments for testing
"""
// XSS vulnerability examples:
document.innerHTML = userInput;  // XSS
document.write(userData);  // XSS
eval(userCode);  // Code injection
"""