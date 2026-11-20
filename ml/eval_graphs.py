
import matplotlib.pyplot as plt

# Graph 1: Overall Model Performance
metrics = ['Precision', 'Recall', 'F1-score', 'ROC-AUC']
scores = [0.851, 0.806, 0.828, 0.970]

plt.figure(figsize=(8, 5))
bars = plt.bar(metrics, scores)

plt.ylim(0, 1.1)
plt.ylabel('Score')
plt.title('Overall Model Performance')

for bar, score in zip(bars, scores):
    plt.text(bar.get_x() + bar.get_width()/2,
             score + 0.02, f'{score:.3f}',
             ha='center')

plt.tight_layout()
plt.savefig('overall_model_performance.png', dpi=300)
plt.show()


# Graph 2: Role-Specific vs Global Baseline
models = ['Role-Specific', 'Global Baseline']
f1_scores = [0.828, 0.798]

plt.figure(figsize=(7, 5))
bars = plt.bar(models, f1_scores)

plt.ylim(0, 1.1)
plt.ylabel('F1-score')
plt.title('Role-Specific Model vs Global Baseline')

for bar, score in zip(bars, f1_scores):
    plt.text(bar.get_x() + bar.get_width()/2,
             score + 0.02, f'{score:.3f}',
             ha='center')

plt.tight_layout()
plt.savefig('model_comparison.png', dpi=300)
plt.show()