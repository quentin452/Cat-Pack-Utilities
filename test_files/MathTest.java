// Simple Java class with atan2 calls
public class MathTest {
    public static void main(String[] args) {
        double angle1 = Math.atan2(1.0, 2.0);
        double angle2 = Math.atan2(3.0, 4.0);
        
        for (int i = 0; i < 10; i++) {
            double result = Math.atan2(i, i + 1);
            System.out.println(result);
        }
        
        // Different method
        Math.sin(1.0);
    }
}