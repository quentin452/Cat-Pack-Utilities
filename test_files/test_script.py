# Test Python file for excessive calls finder

import math

def test_function():
    # Direct calls to atan2
    result1 = math.atan2(1.0, 2.0)
    result2 = math.atan2(3.0, 4.0)
    
    # Multiple calls
    for i in range(50):
        angle = math.atan2(i, i+1)
        print(angle)
    
    # Different variations
    import math as m
    result3 = m.atan2(5.0, 6.0)
    
    # Method calls on objects
    class MathHelper:
        def atan2(self, y, x):
            return math.atan2(y, x)
    
    helper = MathHelper()
    result4 = helper.atan2(7.0, 8.0)
    
    # Not atan2 calls (should not be detected)
    math.sin(1.0)
    math.cos(2.0)

def another_function():
    # More atan2 calls
    math.atan2(9.0, 10.0)
    math.atan2(11.0, 12.0)
    
    # Nested calls
    result = math.atan2(math.atan2(1, 2), math.atan2(3, 4))
    
if __name__ == "__main__":
    test_function()
    another_function()