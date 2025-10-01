// More complex Java class with various atan2 usage patterns
public class ComplexMath {
    private static final double PI = Math.PI;
    
    public double calculateAngle(double x, double y) {
        return Math.atan2(y, x);
    }
    
    public void processAngles() {
        double[] angles = new double[10];
        for (int i = 0; i < angles.length; i++) {
            angles[i] = Math.atan2(i * 2.0, i + 1.0);
        }
        
        // Nested calls
        double complex = Math.atan2(Math.atan2(1.0, 2.0), Math.atan2(3.0, 4.0));
        
        // Static import style (would be Math.atan2 in bytecode)
        double result = Math.atan2(calculateAngle(1, 2), PI);
    }
    
    // Method that doesn't use atan2 (should not be detected)
    public double sine(double x) {
        return Math.sin(x);
    }
}