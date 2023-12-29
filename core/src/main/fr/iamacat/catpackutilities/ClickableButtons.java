package main.fr.iamacat.catpackutilities;

import javax.swing.*;

public class ClickableButtons {
    public static void main(String[] args) {
        createButtons();
    }

    private static void createButtons() {
        JFrame frame = new JFrame("Clickable Buttons");
        frame.setDefaultCloseOperation(JFrame.EXIT_ON_CLOSE);

        JLabel label = new JLabel("Press a button");
        frame.getContentPane().add(label, java.awt.BorderLayout.NORTH);

        JPanel buttonPanel = getjPanel(label);
        frame.getContentPane().add(buttonPanel, java.awt.BorderLayout.CENTER);

        frame.pack();
        frame.setVisible(true);
    }

    private static JPanel getjPanel(JLabel label) {
        JButton button1 = new JButton("Button 1");
        JButton button2 = new JButton("Button 2");
        JButton button3 = new JButton("Button 3");

        button1.addActionListener(e -> label.setText("Button 1 Clicked"));

        button2.addActionListener(e -> label.setText("Button 2 Clicked"));

        button3.addActionListener(e -> label.setText("Button 3 Clicked"));

        JPanel buttonPanel = new JPanel();
        buttonPanel.add(button1);
        buttonPanel.add(button2);
        buttonPanel.add(button3);
        return buttonPanel;
    }
}